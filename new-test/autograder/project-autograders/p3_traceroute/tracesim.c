/*
 * tracesim: LD_PRELOAD shim that replaces raw IPv4 sockets with a simulated
 * network, so the traceroute autograder needs no real network, no root, and
 * no extra container capabilities.
 *
 * socket(AF_INET, SOCK_RAW, proto) returns one end of an AF_UNIX datagram
 * socketpair, so select/poll/epoll/read/recv/SO_RCVTIMEO all behave natively.
 * Packets the program sends are parsed, logged, and "routed" along a path
 * configured through the environment; ICMP responses are written to the
 * other end of every raw IPPROTO_ICMP socket, as the kernel would deliver
 * them (full IPv4 packet, recvfrom() source = IP source address).
 *
 * Environment:
 *   TRACESIM_LOG           JSON-lines event log (required for grading)
 *   TRACESIM_DEST          destination IPv4 address
 *   TRACESIM_HOPS          comma-separated router addresses for TTL 1..N;
 *                          "*" = router that never answers. The destination
 *                          is reached when TTL > N.
 *   TRACESIM_DEST_REPLIES  1 (default) = destination answers echo requests
 *   TRACESIM_NOISE         1 = deliver unrelated ICMP before every response
 */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/uio.h>
#include <time.h>
#include <unistd.h>

#define MAXFD 1024
#define MAXHOPS 128
#define MAXPKT 2048
#define LOCAL_ADDR "10.0.2.15"
#define NOISE_REPLY_FROM "198.51.100.77"
#define NOISE_REQUEST_FROM "198.51.100.88"
#define NOISE_UNREACH_FROM "198.51.100.23"
#define NOISE_UNREACH_ORIG_DST "198.51.100.99"

struct fake {
    int used;
    int proto;
    int hdrincl;
    int ttl;
    int peer;
    int connected;
    struct in_addr conn;
};

static struct fake fakes[MAXFD];
static int logfd = -1;
static struct in_addr local_addr, dest;
static struct in_addr hops[MAXHOPS];
static int hop_silent[MAXHOPS];
static int nhops = 0, dest_replies = 1, noise = 0;

/* Responses generated while no ICMP socket existed yet. */
static unsigned char pending[16][MAXPKT];
static size_t pending_len[16];
static int npending = 0;

static int (*real_socket)(int, int, int);
static int (*real_close)(int);
static ssize_t (*real_sendto)(int, const void *, size_t, int, const struct sockaddr *, socklen_t);
static ssize_t (*real_sendmsg)(int, const struct msghdr *, int);
static ssize_t (*real_send)(int, const void *, size_t, int);
static ssize_t (*real_write)(int, const void *, size_t);
static ssize_t (*real_recvfrom)(int, void *, size_t, int, struct sockaddr *, socklen_t *);
static ssize_t (*real_recvmsg)(int, struct msghdr *, int);
static int (*real_setsockopt)(int, int, int, const void *, socklen_t);
static int (*real_getsockopt)(int, int, int, void *, socklen_t *);
static int (*real_bind)(int, const struct sockaddr *, socklen_t);
static int (*real_connect)(int, const struct sockaddr *, socklen_t);

static void load_real(void) {
    if (real_socket) return;
    real_socket = dlsym(RTLD_NEXT, "socket");
    real_close = dlsym(RTLD_NEXT, "close");
    real_sendto = dlsym(RTLD_NEXT, "sendto");
    real_sendmsg = dlsym(RTLD_NEXT, "sendmsg");
    real_send = dlsym(RTLD_NEXT, "send");
    real_write = dlsym(RTLD_NEXT, "write");
    real_recvfrom = dlsym(RTLD_NEXT, "recvfrom");
    real_recvmsg = dlsym(RTLD_NEXT, "recvmsg");
    real_setsockopt = dlsym(RTLD_NEXT, "setsockopt");
    real_getsockopt = dlsym(RTLD_NEXT, "getsockopt");
    real_bind = dlsym(RTLD_NEXT, "bind");
    real_connect = dlsym(RTLD_NEXT, "connect");
}

static double now(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

static void logf_(const char *fmt, ...) {
    if (logfd < 0) return;
    char buf[1024];
    int n = snprintf(buf, sizeof buf, "{\"t\":%.6f,", now());
    va_list ap;
    va_start(ap, fmt);
    n += vsnprintf(buf + n, sizeof buf - n - 2, fmt, ap);
    va_end(ap);
    if (n > (int)sizeof buf - 3) n = sizeof buf - 3;
    buf[n++] = '}';
    buf[n++] = '\n';
    load_real();
    real_write(logfd, buf, n);
}

static const char *ip(struct in_addr a) {
    static char bufs[4][INET_ADDRSTRLEN];
    static int i = 0;
    i = (i + 1) % 4;
    inet_ntop(AF_INET, &a, bufs[i], sizeof bufs[i]);
    return bufs[i];
}

static int is_fake(int fd) { return fd >= 0 && fd < MAXFD && fakes[fd].used; }

__attribute__((constructor)) static void init(void) {
    load_real();
    inet_pton(AF_INET, LOCAL_ADDR, &local_addr);
    const char *path = getenv("TRACESIM_LOG");
    if (path) logfd = open(path, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0644);
    const char *d = getenv("TRACESIM_DEST");
    if (d) inet_pton(AF_INET, d, &dest);
    const char *r = getenv("TRACESIM_DEST_REPLIES");
    if (r) dest_replies = atoi(r);
    const char *z = getenv("TRACESIM_NOISE");
    if (z) noise = atoi(z);
    const char *h = getenv("TRACESIM_HOPS");
    if (h) {
        char *copy = strdup(h), *save = NULL;
        for (char *tok = strtok_r(copy, ",", &save); tok && nhops < MAXHOPS; tok = strtok_r(NULL, ",", &save)) {
            hop_silent[nhops] = strcmp(tok, "*") == 0;
            if (!hop_silent[nhops]) inet_pton(AF_INET, tok, &hops[nhops]);
            nhops++;
        }
        free(copy);
    }
    logf_("\"ev\":\"init\",\"pid\":%d,\"dest\":\"%s\",\"hops\":%d", (int)getpid(), ip(dest), nhops);
}

/* ------------------------------------------------------------------ packets */

static uint16_t csum(const void *data, size_t len) {
    const unsigned char *p = data;
    uint32_t sum = 0;
    for (; len > 1; len -= 2, p += 2) sum += (p[0] << 8) | p[1];
    if (len) sum += p[0] << 8;
    while (sum >> 16) sum = (sum & 0xffff) + (sum >> 16);
    return htons((uint16_t)~sum);
}

static size_t make_icmp(unsigned char *out, struct in_addr src, uint8_t type, uint8_t code,
                        const unsigned char rest[4], const unsigned char *body, size_t bodylen) {
    if (bodylen > MAXPKT - 28) bodylen = MAXPKT - 28;
    size_t total = 28 + bodylen;
    memset(out, 0, 28);
    out[0] = 0x45;
    uint16_t tl = htons((uint16_t)total);
    memcpy(out + 2, &tl, 2);
    out[8] = 64;
    out[9] = IPPROTO_ICMP;
    memcpy(out + 12, &src, 4);
    memcpy(out + 16, &local_addr, 4);
    uint16_t c = csum(out, 20);
    memcpy(out + 10, &c, 2);
    out[20] = type;
    out[21] = code;
    if (rest) memcpy(out + 24, rest, 4);
    if (bodylen) memcpy(out + 28, body, bodylen);
    c = csum(out + 20, total - 20);
    memcpy(out + 22, &c, 2);
    return total;
}

static void deliver(const unsigned char *pkt, size_t len, const char *kind) {
    int delivered = 0;
    for (int fd = 0; fd < MAXFD; fd++) {
        if (fakes[fd].used && fakes[fd].proto == IPPROTO_ICMP) {
            load_real();
            if (real_send(fakes[fd].peer, pkt, len, MSG_DONTWAIT) >= 0) delivered++;
        }
    }
    if (!delivered && npending < 16) {
        memcpy(pending[npending], pkt, len);
        pending_len[npending++] = len;
    }
    struct in_addr from;
    memcpy(&from, pkt + 12, 4);
    logf_("\"ev\":\"deliver\",\"kind\":\"%s\",\"from\":\"%s\",\"type\":%d,\"code\":%d,\"sockets\":%d",
          kind, ip(from), pkt[20], pkt[21], delivered);
}

static void send_noise(void) {
    unsigned char pkt[MAXPKT], body[64];
    struct in_addr a;
    /* Echo reply from a host that is not the destination. */
    unsigned char rest[4] = {0xbe, 0xef, 0x00, 0x07};
    memset(body, 'N', 32);
    inet_pton(AF_INET, NOISE_REPLY_FROM, &a);
    deliver(pkt, make_icmp(pkt, a, 0, 0, rest, body, 32), "noise");
    /* Echo request from some other host (e.g. someone pinging us). */
    inet_pton(AF_INET, NOISE_REQUEST_FROM, &a);
    deliver(pkt, make_icmp(pkt, a, 8, 0, rest, body, 32), "noise");
    /* Destination unreachable about an unrelated flow. */
    unsigned char orig[28] = {0x45, 0, 0, 28, 0, 0, 0, 0, 3, 17};
    memcpy(orig + 12, &local_addr, 4);
    inet_pton(AF_INET, NOISE_UNREACH_ORIG_DST, orig + 16);
    inet_pton(AF_INET, NOISE_UNREACH_FROM, &a);
    deliver(pkt, make_icmp(pkt, a, 3, 1, NULL, orig, sizeof orig), "noise");
}

static void route(unsigned char *pkt, size_t len) {
    int ihl = (pkt[0] & 15) * 4;
    int ttl = pkt[8], proto = pkt[9];
    struct in_addr daddr;
    memcpy(&daddr, pkt + 16, 4);
    const unsigned char *icmp = pkt + ihl;
    size_t icmplen = len - ihl;
    unsigned char out[MAXPKT];

    if (daddr.s_addr != dest.s_addr) {
        logf_("\"ev\":\"drop\",\"reason\":\"destination %s is not the traced host %s\"", ip(daddr), ip(dest));
        return;
    }
    if (ttl == 0) {
        logf_("\"ev\":\"drop\",\"reason\":\"TTL 0\"");
        return;
    }
    if (ttl <= nhops) {
        if (hop_silent[ttl - 1]) {
            logf_("\"ev\":\"silent\",\"ttl\":%d", ttl);
            return;
        }
        if (noise) send_noise();
        /* Quote the datagram as the router saw it (TTL 1), as RFC 1812 routers do. */
        unsigned char quoted[576];
        size_t qlen = len < sizeof quoted ? len : sizeof quoted;
        memcpy(quoted, pkt, qlen);
        quoted[8] = 1;
        quoted[10] = quoted[11] = 0;
        uint16_t c = csum(quoted, ihl);
        memcpy(quoted + 10, &c, 2);
        deliver(out, make_icmp(out, hops[ttl - 1], 11, 0, NULL, quoted, qlen), "time_exceeded");
        return;
    }
    const char *why = NULL;
    if (!dest_replies) why = "destination does not answer";
    else if (proto != IPPROTO_ICMP) why = "IP protocol is not ICMP";
    else if (icmplen < 8) why = "ICMP message too short";
    else if (icmp[0] != 8 || icmp[1] != 0) why = "not an ICMP echo request";
    else if (csum(icmp, icmplen) != 0) why = "bad ICMP checksum";
    if (why) {
        logf_("\"ev\":\"no_reply\",\"ttl\":%d,\"reason\":\"%s\"", ttl, why);
        return;
    }
    if (noise) send_noise();
    deliver(out, make_icmp(out, dest, 0, 0, icmp + 4, icmp + 8, icmplen - 8), "echo_reply");
}

static void handle_send(int fd, const void *buf, size_t len, struct in_addr to) {
    struct fake *f = &fakes[fd];
    unsigned char pkt[MAXPKT];
    const unsigned char *b = buf;
    size_t total;
    int built_ip = f->hdrincl || f->proto == IPPROTO_RAW;

    if (built_ip) {
        int ver = len ? b[0] >> 4 : 0, ihl = len ? (b[0] & 15) * 4 : 0;
        if (len < 20 || ver != 4 || ihl < 20 || (size_t)ihl > len || len > MAXPKT) {
            logf_("\"ev\":\"send\",\"fd\":%d,\"len\":%zu,\"built_ip\":1,\"ip_ok\":0,\"ver\":%d,\"ihl\":%d",
                  fd, len, ver, ihl);
            return;
        }
        memcpy(pkt, b, len);
        total = len;
        /* Fields the kernel fills in for IP_HDRINCL sockets. */
        uint16_t tl = htons((uint16_t)total);
        memcpy(pkt + 2, &tl, 2);
        if (!pkt[4] && !pkt[5]) pkt[5] = 1;
        uint32_t zero = 0;
        if (!memcmp(pkt + 12, &zero, 4)) memcpy(pkt + 12, &local_addr, 4);
        if (!memcmp(pkt + 16, &zero, 4)) memcpy(pkt + 16, &to, 4);
        pkt[10] = pkt[11] = 0;
        uint16_t c = csum(pkt, ihl);
        memcpy(pkt + 10, &c, 2);
    } else {
        if (len + 20 > MAXPKT) len = MAXPKT - 20;
        memset(pkt, 0, 20);
        pkt[0] = 0x45;
        pkt[8] = (unsigned char)f->ttl;
        pkt[9] = (unsigned char)f->proto;
        memcpy(pkt + 12, &local_addr, 4);
        memcpy(pkt + 16, &to, 4);
        memcpy(pkt + 20, b, len);
        total = len + 20;
    }
    int ihl = (pkt[0] & 15) * 4;
    const unsigned char *icmp = pkt + ihl;
    size_t icmplen = total - ihl;
    struct in_addr daddr;
    memcpy(&daddr, pkt + 16, 4);
    int ck = icmplen >= 8 && csum(icmp, icmplen) == 0;
    logf_("\"ev\":\"send\",\"fd\":%d,\"len\":%zu,\"built_ip\":%d,\"ip_ok\":1,\"ver\":4,\"ihl\":%d,"
          "\"ttl\":%d,\"proto\":%d,\"dst\":\"%s\",\"sendto\":\"%s\",\"type\":%d,\"code\":%d,"
          "\"cksum_ok\":%d,\"id\":%d,\"seq\":%d",
          fd, total, built_ip, ihl, pkt[8], pkt[9], ip(daddr), ip(to),
          icmplen >= 1 ? icmp[0] : -1, icmplen >= 2 ? icmp[1] : -1, ck,
          icmplen >= 8 ? (icmp[4] << 8 | icmp[5]) : -1, icmplen >= 8 ? (icmp[6] << 8 | icmp[7]) : -1);
    route(pkt, total);
}

/* --------------------------------------------------------------- wrappers */

int socket(int domain, int type, int protocol) {
    load_real();
    if (domain != AF_INET || (type & 0xf) != SOCK_RAW) return real_socket(domain, type, protocol);
    if (protocol == 0) {
        errno = EPROTONOSUPPORT;
        return -1;
    }
    int sv[2];
    if (socketpair(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC, 0, sv) < 0) return -1;
    int peer = fcntl(sv[1], F_DUPFD_CLOEXEC, 600);
    real_close(sv[1]);
    if (peer < 0 || sv[0] >= MAXFD) {
        real_close(sv[0]);
        if (peer >= 0) real_close(peer);
        errno = EMFILE;
        return -1;
    }
    fcntl(peer, F_SETFL, O_NONBLOCK);
    if (!(type & SOCK_CLOEXEC)) fcntl(sv[0], F_SETFD, 0);
    if (type & SOCK_NONBLOCK) fcntl(sv[0], F_SETFL, fcntl(sv[0], F_GETFL) | O_NONBLOCK);
    fakes[sv[0]] = (struct fake){.used = 1, .proto = protocol, .ttl = 64, .peer = peer};
    logf_("\"ev\":\"socket\",\"fd\":%d,\"proto\":%d", sv[0], protocol);
    if (protocol == IPPROTO_ICMP) {
        for (int i = 0; i < npending; i++) real_send(peer, pending[i], pending_len[i], MSG_DONTWAIT);
        npending = 0;
    }
    return sv[0];
}

int close(int fd) {
    load_real();
    if (is_fake(fd)) {
        real_close(fakes[fd].peer);
        fakes[fd].used = 0;
        logf_("\"ev\":\"close\",\"fd\":%d", fd);
    }
    return real_close(fd);
}

ssize_t sendto(int fd, const void *buf, size_t len, int flags, const struct sockaddr *addr, socklen_t alen) {
    load_real();
    if (!is_fake(fd)) return real_sendto(fd, buf, len, flags, addr, alen);
    struct in_addr to = {0};
    if (addr && alen >= sizeof(struct sockaddr_in) && addr->sa_family == AF_INET) {
        to = ((const struct sockaddr_in *)addr)->sin_addr;
    } else if (fakes[fd].connected) {
        to = fakes[fd].conn;
    } else {
        errno = EDESTADDRREQ;
        return -1;
    }
    handle_send(fd, buf, len, to);
    return (ssize_t)len;
}

ssize_t send(int fd, const void *buf, size_t len, int flags) {
    load_real();
    if (!is_fake(fd)) return real_send(fd, buf, len, flags);
    return sendto(fd, buf, len, flags, NULL, 0);
}

ssize_t write(int fd, const void *buf, size_t len) {
    load_real();
    if (!is_fake(fd)) return real_write(fd, buf, len);
    return sendto(fd, buf, len, 0, NULL, 0);
}

ssize_t sendmsg(int fd, const struct msghdr *msg, int flags) {
    load_real();
    if (!is_fake(fd)) return real_sendmsg(fd, msg, flags);
    unsigned char buf[MAXPKT];
    size_t n = 0;
    for (size_t i = 0; i < msg->msg_iovlen && n < sizeof buf; i++) {
        size_t c = msg->msg_iov[i].iov_len;
        if (c > sizeof buf - n) c = sizeof buf - n;
        memcpy(buf + n, msg->msg_iov[i].iov_base, c);
        n += c;
    }
    return sendto(fd, buf, n, flags, msg->msg_name, msg->msg_namelen);
}

static void fill_src(const unsigned char *pkt, ssize_t n, struct sockaddr *src, socklen_t *slen) {
    if (!src || !slen) return;
    struct sockaddr_in sin = {.sin_family = AF_INET};
    if (n >= 20) memcpy(&sin.sin_addr, pkt + 12, 4);
    socklen_t c = *slen < sizeof sin ? *slen : sizeof sin;
    memcpy(src, &sin, c);
    *slen = sizeof sin;
}

ssize_t recvfrom(int fd, void *buf, size_t len, int flags, struct sockaddr *src, socklen_t *slen) {
    load_real();
    if (!is_fake(fd)) return real_recvfrom(fd, buf, len, flags, src, slen);
    static unsigned char tmp[65536];
    ssize_t n = real_recvfrom(fd, tmp, sizeof tmp, flags & ~MSG_TRUNC, NULL, NULL);
    if (n < 0) return n;
    size_t c = (size_t)n < len ? (size_t)n : len;
    memcpy(buf, tmp, c);
    fill_src(tmp, n, src, slen);
    return (flags & MSG_TRUNC) ? n : (ssize_t)c;
}

ssize_t __recvfrom_chk(int fd, void *buf, size_t len, size_t buflen, int flags, struct sockaddr *src,
                       socklen_t *slen) {
    if (len > buflen) abort();
    return recvfrom(fd, buf, len, flags, src, slen);
}

ssize_t recvmsg(int fd, struct msghdr *msg, int flags) {
    load_real();
    if (!is_fake(fd)) return real_recvmsg(fd, msg, flags);
    void *name = msg->msg_name;
    socklen_t namelen = msg->msg_namelen;
    msg->msg_name = NULL;
    msg->msg_namelen = 0;
    msg->msg_controllen = 0;
    ssize_t n = real_recvmsg(fd, msg, flags);
    msg->msg_name = name;
    msg->msg_namelen = namelen;
    if (n >= 0 && name && msg->msg_iovlen) {
        fill_src(msg->msg_iov[0].iov_base, (ssize_t)msg->msg_iov[0].iov_len < n ? (ssize_t)msg->msg_iov[0].iov_len : n,
                 name, &msg->msg_namelen);
    }
    return n;
}

int setsockopt(int fd, int level, int opt, const void *val, socklen_t len) {
    load_real();
    if (!is_fake(fd)) return real_setsockopt(fd, level, opt, val, len);
    if (level == IPPROTO_IP) {
        int v = (val && len >= sizeof(int)) ? *(const int *)val : (val && len >= 1 ? *(const unsigned char *)val : 0);
        if (opt == IP_HDRINCL) fakes[fd].hdrincl = v != 0;
        if (opt == IP_TTL) fakes[fd].ttl = v;
        logf_("\"ev\":\"setsockopt\",\"fd\":%d,\"opt\":%d,\"value\":%d", fd, opt, v);
        return 0;
    }
    if (level == SOL_SOCKET) {
        int r = real_setsockopt(fd, level, opt, val, len);
        return (r < 0 && opt != SO_RCVTIMEO && opt != SO_SNDTIMEO) ? 0 : r;
    }
    return 0;
}

int getsockopt(int fd, int level, int opt, void *val, socklen_t *len) {
    load_real();
    if (!is_fake(fd) || level == SOL_SOCKET) return real_getsockopt(fd, level, opt, val, len);
    int v = 0;
    if (level == IPPROTO_IP && opt == IP_HDRINCL) v = fakes[fd].hdrincl || fakes[fd].proto == IPPROTO_RAW;
    if (level == IPPROTO_IP && opt == IP_TTL) v = fakes[fd].ttl;
    if (val && len && *len >= sizeof v) {
        memcpy(val, &v, sizeof v);
        *len = sizeof v;
    }
    return 0;
}

int bind(int fd, const struct sockaddr *addr, socklen_t len) {
    load_real();
    return is_fake(fd) ? 0 : real_bind(fd, addr, len);
}

int connect(int fd, const struct sockaddr *addr, socklen_t len) {
    load_real();
    if (!is_fake(fd)) return real_connect(fd, addr, len);
    if (addr && len >= sizeof(struct sockaddr_in) && addr->sa_family == AF_INET) {
        fakes[fd].connected = 1;
        fakes[fd].conn = ((const struct sockaddr_in *)addr)->sin_addr;
    }
    return 0;
}

/* Programs that refuse to run unless root: the simulated sockets need no privilege. */
uid_t getuid(void) { return 0; }
uid_t geteuid(void) { return 0; }
