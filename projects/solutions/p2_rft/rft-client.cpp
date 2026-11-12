// Reference rft-client used to validate the Project 2 autograder (not shipped to students).
// Go-Back-N sender (Kurose & Ross Fig. 3.20) on the instructor's support files.
// The BUG_* defines inject common student mistakes.
#include <unistd.h>

#include <chrono>
#include <cstring>
#include <fstream>
#include <iostream>
#include <vector>

#include "datagram.h"
#include "logging.h"
#include "timerC.h"
#include "unreliableTransport.h"

int main(int argc, char *argv[]) {
  uint16_t portNum = 12345;
  std::string hostname = "localhost";
  std::string inputFilename;
#if defined(BUG_WINDOW_20)
  unsigned window = 20;
#elif defined(BUG_STOP_AND_WAIT)
  unsigned window = 1;
#else
  unsigned window = 10;
#endif
  int timeoutMs = 60;

  int opt;
  while ((opt = getopt(argc, argv, "f:h:p:d:w:t:")) != -1) {
    switch (opt) {
      case 'p': portNum = std::stoi(optarg); break;
      case 'h': hostname = optarg; break;
#ifndef BUG_NO_DEBUG_FLAG
      case 'd': LOG_LEVEL = std::stoi(optarg); break;
#endif
      case 'f': inputFilename = optarg; break;
#ifndef BUG_IGNORE_W
      case 'w': window = std::stoi(optarg); break;
#endif
#ifndef BUG_IGNORE_T
      case 't': timeoutMs = std::stoi(optarg); break;
#endif
      default: break;
    }
  }
  if (inputFilename.empty()) {
    FATAL << "-f filename is required" << ENDL;
    return 1;
  }
  std::ifstream in(inputFilename, std::ios::binary);
  if (!in) {
    FATAL << "cannot open " << inputFilename << ENDL;
    return 1;
  }
#ifdef BUG_COMPILE_ERROR
  this is not C++;
#endif

  auto start = std::chrono::steady_clock::now();
  size_t bytesSent = 0;
  try {
    unreliableTransportC conn(hostname, portNum);
    timerC timer(timeoutMs);
    std::vector<datagramS> sndpkt(window);
#ifdef BUG_SEQ_FROM_0
    uint32_t base = 0, nextseqnum = 0;
#else
    uint32_t base = 1, nextseqnum = 1;
#endif
    uint32_t eofSeq = 0;  // sequence number of the zero-length datagram once sent
    bool finished = false;

    while (!finished) {
      // Room in the window: read the next chunk and send it.
      if (nextseqnum < base + window && eofSeq == 0) {
        datagramS &pkt = sndpkt[nextseqnum % window];
        pkt = datagramS{};
        pkt.seqNum = nextseqnum;
#ifdef BUG_TEXT_READ
        std::string line;
        if (std::getline(in, line)) line = line.substr(0, MAX_PAYLOAD_LENGTH);
        memcpy(pkt.data, line.data(), line.size());
        pkt.payloadLength = line.size();
        if (!in && line.empty()) pkt.payloadLength = 0;
#else
        in.read(reinterpret_cast<char *>(pkt.data), MAX_PAYLOAD_LENGTH);
        pkt.payloadLength = in.gcount();
#endif
        bytesSent += pkt.payloadLength;
        pkt.checksum = computeChecksum(pkt);
        if (pkt.payloadLength == 0) eofSeq = nextseqnum;
        conn.udt_send(pkt);
        TRACE << "sent " << toString(pkt) << ENDL;
        if (base == nextseqnum) timer.start();
        nextseqnum++;
#ifdef BUG_NO_EOF_WAIT
        if (eofSeq) break;
#endif
      }

      // ACKs from the server.
      datagramS ack{};
      if (conn.udt_receive(ack) > 0) {
#ifdef BUG_NO_ACK_CHECKSUM
        bool valid = true;
#else
        bool valid = validateChecksum(ack);
#endif
        if (valid && ack.ackNum + 1 > base) {
          base = ack.ackNum + 1;
          DEBUG << "ACK " << ack.ackNum << ", base now " << base << ENDL;
          if (eofSeq && base > eofSeq) finished = true;
          if (base == nextseqnum) timer.stop();
          else timer.start();
        } else if (!valid) {
          WARNING << "corrupted ACK ignored" << ENDL;
        }
      }

#ifndef BUG_NO_TIMEOUT
      if (timer.timeout()) {
        INFO << "timeout, resending " << base << ".." << nextseqnum - 1 << ENDL;
        timer.start();
        for (uint32_t s = base; s < nextseqnum; s++) conn.udt_send(sndpkt[s % window]);
      }
#endif
    }
  } catch (std::exception &e) {
    FATAL << "Error: " << e.what() << ENDL;
    return 1;
  }

  double secs = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
#ifndef BUG_NO_REPORT
  std::cout << "Transferred " << bytesSent << " bytes in " << secs << " seconds; throughput "
            << (secs > 0 ? bytesSent / secs : 0) << " bytes/sec" << std::endl;
#endif
  return 0;
}
