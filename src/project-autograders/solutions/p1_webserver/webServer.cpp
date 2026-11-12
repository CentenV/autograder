// Reference webServer used to validate the Project 1 autograder (not shipped to students).
// -DCOURSE_560 adds HEAD/POST; the BUG_* defines inject common student mistakes.
#include "webServer.h"

#include <cerrno>
#include <cstdlib>
#include <string>
#include <vector>

static int listenFd = -1;

static void sigHandler(int) {
  if (listenFd >= 0) close(listenFd);
  _exit(0);
}

struct Request {
  int status = 400;
  int method = 0;
  std::string filename;
  std::vector<char> leftover;  // bytes read past the end of the header (POST body)
  size_t contentLength = 0;
};

static bool writeAll(int fd, const char *buf, size_t len) {
  while (len > 0) {
    ssize_t n = write(fd, buf, len);
    if (n <= 0) return false;
    buf += n;
    len -= n;
  }
  return true;
}

void sendLine(int fd, const std::string &line) {
  std::string out = line + "\r\n";
  writeAll(fd, out.data(), out.size());
}

// Returns false if the client closed the connection before a full header arrived.
static bool readHeader(int fd, std::string &header, std::vector<char> &leftover) {
  std::string buf;
#ifdef BUG_SINGLE_READ
  char big[4096];
  ssize_t n = read(fd, big, sizeof big);
  if (n <= 0) return false;
  buf.append(big, n);
#else
  char chunk[10];
  while (buf.find("\r\n\r\n") == std::string::npos) {
    ssize_t n = read(fd, chunk, sizeof chunk);
#ifdef BUG_EOF_SPIN
    if (n < 0) return false;
#else
    if (n <= 0) return false;
#endif
    buf.append(chunk, n);
  }
#endif
  size_t end = buf.find("\r\n\r\n");
  if (end == std::string::npos) {  // only reachable with BUG_SINGLE_READ
    header = buf;
    return true;
  }
  header = buf.substr(0, end + 4);
  leftover.assign(buf.begin() + end + 4, buf.end());
  return true;
}

static Request parseRequest(int fd, bool &ok) {
  Request req;
  std::string header;
  ok = readHeader(fd, header, req.leftover);
  if (!ok) return req;
  std::string first = header.substr(0, header.find("\r\n"));
  std::smatch m;
  static const std::regex requestLine(R"(^([A-Z]+) (/\S*) HTTP/\d\.\d$)");
  if (!std::regex_match(first, m, requestLine)) return req;
  std::string method = m[1], uri = m[2];
  if (method == "GET") req.method = GET;
#ifdef COURSE_560
  else if (method == "HEAD") req.method = HEAD;
  else if (method == "POST") req.method = POST;
#endif
  else return req;

#ifdef BUG_NO_FILTER
  req.filename = "data" + uri;
  req.status = 200;
#else
  static const std::regex allowed(R"(^/((file[0-9]\.html)|(image[0-9]\.jpg))$)");
  if (std::regex_match(uri, allowed)) {
    req.filename = "data" + uri;
    req.status = 200;
  } else {
    req.status = 404;
  }
#endif
  std::smatch cl;
  static const std::regex lengthRe(R"(\r\n[Cc]ontent-[Ll]ength:\s*(\d+))");
  if (std::regex_search(header, cl, lengthRe)) req.contentLength = std::stoul(cl[1]);
  return req;
}

void send404(int fd) {
  sendLine(fd, "HTTP/1.0 404 Not Found");
  sendLine(fd, "Content-Type: text/html");
  sendLine(fd, "");
  sendLine(fd, "<html><body>404: file not found</body></html>");
}

void send400(int fd) {
  sendLine(fd, "HTTP/1.0 400 Bad Request");
  sendLine(fd, "");
}

static const char *contentType(const std::string &name) {
  return name.size() > 4 && name.compare(name.size() - 4, 4, ".jpg") == 0 ? "image/jpeg" : "text/html";
}

void sendFile(int fd, const std::string &filename, bool withBody) {
  struct stat st {};
  if (stat(filename.c_str(), &st) != 0 || !S_ISREG(st.st_mode)) {
    send404(fd);
    return;
  }
  sendLine(fd, "HTTP/1.0 200 OK");
  sendLine(fd, std::string("Content-Type: ") + contentType(filename));
#ifndef BUG_NO_CONTENT_LENGTH
  sendLine(fd, "Content-Length: " + std::to_string(st.st_size));
#endif
  sendLine(fd, "");
  if (!withBody) return;
  int file = open(filename.c_str(), O_RDONLY);
  if (file < 0) return;
#ifdef BUG_TEXT_MODE
  std::string contents;
  char c[10];
  ssize_t n;
  while ((n = read(file, c, sizeof c)) > 0) contents.append(c, n);
  writeAll(fd, contents.c_str(), strlen(contents.c_str()));
#else
  char buf[10];
  ssize_t n;
  while ((n = read(file, buf, sizeof buf)) > 0)
    if (!writeAll(fd, buf, n)) break;
#endif
  close(file);
}

#ifdef COURSE_560
static void saveFile(int fd, Request &req) {
  struct stat st {};
  bool existed = stat(req.filename.c_str(), &st) == 0;
  int out = open(req.filename.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
  if (out < 0) {
    send400(fd);
    return;
  }
  size_t have = std::min(req.leftover.size(), req.contentLength);
  writeAll(out, req.leftover.data(), have);
  char buf[10];
  while (have < req.contentLength) {
    ssize_t n = read(fd, buf, std::min(sizeof buf, req.contentLength - have));
    if (n <= 0) break;
    writeAll(out, buf, n);
    have += n;
  }
  close(out);
  sendLine(fd, existed ? "HTTP/1.0 200 OK" : "HTTP/1.0 201 Created");
  sendLine(fd, "Content-Length: 0");
  sendLine(fd, "");
}
#endif

int processConnection(int fd) {
  bool ok;
  Request req = parseRequest(fd, ok);
  if (!ok) return 0;
  if (req.status == 400) send400(fd);
  else if (req.status == 404) send404(fd);
#ifdef COURSE_560
  else if (req.method == POST) saveFile(fd, req);
#endif
  else sendFile(fd, req.filename, req.method != HEAD);
  return 0;
}

int main(int argc, char *argv[]) {
  int opt;
  while ((opt = getopt(argc, argv, "d:")) != -1) {
    if (opt == 'd') LOG_LEVEL = std::stoi(optarg);
    else {
      std::cout << "usage: " << argv[0] << " -d LOG_LEVEL" << std::endl;
      exit(-1);
    }
  }
#ifndef BUG_NO_SIGINT
  signal(SIGINT, sigHandler);
#endif
  signal(SIGPIPE, SIG_IGN);

  listenFd = socket(PF_INET, SOCK_STREAM, 0);
  if (listenFd < 0) {
    perror("socket");
    return 1;
  }
  struct sockaddr_in addr {};
  addr.sin_family = AF_INET;
  addr.sin_addr.s_addr = htonl(INADDR_ANY);
  uint16_t port = 1701;
  for (;;) {
    addr.sin_port = htons(port);
    if (bind(listenFd, (struct sockaddr *)&addr, sizeof addr) == 0) break;
    if (errno != EADDRINUSE) {
      perror("bind");
      return 1;
    }
    port++;
  }
#ifdef BUG_PORT_NOT_FLUSHED
  printf("Using port: %d", port);
#else
  std::cout << "Using port: " << port << std::endl;
#endif
  if (listen(listenFd, 10) < 0) {
    perror("listen");
    return 1;
  }
#ifdef BUG_COMPILE_ERROR
  this is not C++;
#endif
  for (;;) {
    int connFd = accept(listenFd, nullptr, nullptr);
    if (connFd < 0) continue;
    processConnection(connFd);
    close(connFd);
#ifdef BUG_EXIT_AFTER_ONE
    return 0;
#endif
  }
}
