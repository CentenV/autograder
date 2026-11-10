// Reference "student" solution for Project 3, used only to validate the
// autograder. NOT for distribution to students.
//
// Compile-time switches create deliberately broken variants (see variants.json):
//   BUG_TTL_FROM_1     first datagram has TTL 1
//   BUG_NO_FILTER      treats any Echo Reply as the destination's, and any
//                      ICMP message as a router response
//   BUG_BLOCKING       blocking recvfrom() with no timeout (hangs on silent hops)
//   BUG_BAD_CHECKSUM   never fills in the ICMP checksum
//   BUG_NO_LIMIT       keeps going to TTL 64
//   BUG_COMPILE_ERROR  fails to compile
#include <arpa/inet.h>
#include <netinet/ip.h>
#include <netinet/ip_icmp.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <unistd.h>

#include <chrono>
#include <cstring>
#include <iostream>
#include <string>

#ifdef BUG_COMPILE_ERROR
#error "deliberate compile error"
#endif

static int LOG_LEVEL = 0;
#define DEBUG if (LOG_LEVEL > 4) std::cout << "DEBUG: "

static const int PACKET_SIZE = 64;

#ifdef BUG_TTL_FROM_1
static const int FIRST_TTL = 1;
#else
static const int FIRST_TTL = 2;
#endif
#ifdef BUG_NO_LIMIT
static const int LAST_TTL = 64;
#else
static const int LAST_TTL = 31;
#endif

static uint16_t checksum(const void *data, int len) {
    const uint16_t *p = static_cast<const uint16_t *>(data);
    uint32_t sum = 0;
    for (; len > 1; len -= 2) sum += *p++;
    if (len) sum += *reinterpret_cast<const uint8_t *>(p);
    while (sum >> 16) sum = (sum & 0xffff) + (sum >> 16);
    return static_cast<uint16_t>(~sum);
}

int main(int argc, char *argv[]) {
    std::string target;
    int opt;
    while ((opt = getopt(argc, argv, "t:d:")) != -1) {
        if (opt == 't') target = optarg;
        else if (opt == 'd') LOG_LEVEL = std::stoi(optarg);
    }
    if (target.empty()) {
        std::cerr << "usage: traceroute -t IP [-d LEVEL]\n";
        return 1;
    }
    DEBUG << "target " << target << std::endl;

    sockaddr_in dest{};
    dest.sin_family = AF_INET;
    dest.sin_addr.s_addr = inet_addr(target.c_str());

    char sendBuf[PACKET_SIZE], recvBuf[1500];
    memset(sendBuf, 'A', sizeof sendBuf);
    auto *ip = reinterpret_cast<iphdr *>(sendBuf);
    ip->version = 4;
    ip->ihl = 5;
    ip->tos = 0;
    ip->tot_len = htons(PACKET_SIZE);
    ip->id = 0;
    ip->frag_off = 0;
    ip->protocol = IPPROTO_ICMP;
    ip->check = 0;
    ip->saddr = 0;
    ip->daddr = dest.sin_addr.s_addr;
    auto *icmp = reinterpret_cast<icmphdr *>(sendBuf + sizeof(iphdr));
    icmp->type = ICMP_ECHO;
    icmp->code = 0;
    icmp->un.echo.id = htons(getpid() & 0xffff);

    int sendFd = socket(AF_INET, SOCK_RAW, IPPROTO_RAW);
    int recvFd = socket(AF_INET, SOCK_RAW, IPPROTO_ICMP);
    if (sendFd < 0 || recvFd < 0) {
        perror("socket");
        return 1;
    }
    DEBUG << "sockets " << sendFd << " " << recvFd << std::endl;

    bool replied = false;
    for (int ttl = FIRST_TTL; ttl <= LAST_TTL && !replied; ttl++) {
        ip->ttl = ttl;
        icmp->un.echo.sequence = htons(ttl);
        icmp->checksum = 0;
#ifndef BUG_BAD_CHECKSUM
        icmp->checksum = checksum(icmp, PACKET_SIZE - sizeof(iphdr));
#endif
        if (sendto(sendFd, sendBuf, PACKET_SIZE, 0, reinterpret_cast<sockaddr *>(&dest), sizeof dest) < 0) {
            perror("sendto");
            return 1;
        }
        DEBUG << "sent TTL " << ttl << std::endl;

        auto start = std::chrono::steady_clock::now();
        bool done = false;
        while (!done && std::chrono::steady_clock::now() - start < std::chrono::seconds(15)) {
#ifndef BUG_BLOCKING
            fd_set fds;
            FD_ZERO(&fds);
            FD_SET(recvFd, &fds);
            timeval tv{5, 0};
            if (select(recvFd + 1, &fds, nullptr, nullptr, &tv) < 0) {
                perror("select");
                return 1;
            }
            if (!FD_ISSET(recvFd, &fds)) continue;
#endif
            sockaddr_in from{};
            socklen_t fromLen = sizeof from;
            ssize_t n = recvfrom(recvFd, recvBuf, sizeof recvBuf, 0, reinterpret_cast<sockaddr *>(&from), &fromLen);
            if (n < static_cast<ssize_t>(sizeof(iphdr) + sizeof(icmphdr))) continue;
            auto *rip = reinterpret_cast<iphdr *>(recvBuf);
            auto *ricmp = reinterpret_cast<icmphdr *>(recvBuf + rip->ihl * 4);
            char addr[INET_ADDRSTRLEN];
            inet_ntop(AF_INET, &from.sin_addr, addr, sizeof addr);
            DEBUG << "got ICMP type " << int(ricmp->type) << " from " << addr << std::endl;
#ifdef BUG_NO_FILTER
            if (ricmp->type == ICMP_ECHOREPLY) {
#else
            if (ricmp->type == ICMP_ECHOREPLY && from.sin_addr.s_addr == dest.sin_addr.s_addr) {
#endif
                std::cout << ttl << "  " << addr << "  reached destination" << std::endl;
                replied = done = true;
#ifdef BUG_NO_FILTER
            } else {
#else
            } else if (ricmp->type == ICMP_TIME_EXCEEDED && ricmp->code == ICMP_EXC_TTL) {
#endif
                std::cout << ttl << "  " << addr << std::endl;
                done = true;
            }
        }
        if (!done) std::cout << "No response with TTL of " << ttl << std::endl;
    }
    close(sendFd);
    close(recvFd);
    return 0;
}
