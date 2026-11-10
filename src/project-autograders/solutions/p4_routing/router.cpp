// Reference "student" solution for Project 4, used only to validate the
// autograder. NOT for distribution to students.
//
// Compile-time switches create deliberately broken variants (see variants.json):
//   BUG_FIRST_MATCH   first matching static route wins instead of longest prefix
//   BUG_NO_TRIM       doesn't strip whitespace / indented comments
//   BUG_HANG          never exits
//   BUG_COMPILE_ERROR fails to compile
#include <cstdint>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>
#include <unistd.h>

#ifdef BUG_COMPILE_ERROR
#error "deliberate compile error"
#endif

namespace {

struct Net {
    uint32_t addr = 0;
    int len = 0;
    uint32_t mask() const { return len == 0 ? 0 : 0xFFFFFFFFu << (32 - len); }
    bool contains(uint32_t ip) const { return (ip & mask()) == (addr & mask()); }
};

struct Iface { std::string name; Net net; };
struct Route { Net net; uint32_t nextHop; };

bool parseIp(const std::string &s, uint32_t &out) {
    std::istringstream in(s);
    uint32_t result = 0;
    for (int i = 0; i < 4; ++i) {
        int octet;
        if (!(in >> octet) || octet < 0 || octet > 255) return false;
        result = (result << 8) | static_cast<uint32_t>(octet);
        if (i < 3 && in.get() != '.') return false;
    }
    out = result;
    return true;
}

bool parseCidr(const std::string &s, Net &net) {
    auto slash = s.find('/');
    if (slash == std::string::npos) return false;
    net.len = std::stoi(s.substr(slash + 1));
    return parseIp(s.substr(0, slash), net.addr);
}

std::string ipToString(uint32_t ip) {
    return std::to_string(ip >> 24) + "." + std::to_string((ip >> 16) & 255) + "." +
           std::to_string((ip >> 8) & 255) + "." + std::to_string(ip & 255);
}

std::vector<std::string> contentLines(std::istream &in) {
    std::vector<std::string> lines;
    std::string line;
    while (std::getline(in, line)) {
#ifndef BUG_NO_TRIM
        auto b = line.find_first_not_of(" \t\r");
        if (b == std::string::npos) continue;
        line = line.substr(b, line.find_last_not_of(" \t\r") - b + 1);
#endif
        if (line.empty() || line[0] == '#') continue;
        lines.push_back(line);
    }
    return lines;
}

const Iface *egress(const std::vector<Iface> &ifaces, uint32_t ip) {
    const Iface *best = nullptr;
    for (const auto &i : ifaces)
        if (i.net.contains(ip) && (!best || i.net.len > best->net.len)) best = &i;
    return best;
}

}  // namespace

int main(int argc, char *argv[]) {
    std::string cfg, rt, inPath, outPath;
    int opt;
    while ((opt = getopt(argc, argv, "c:r:i:o:d:")) != -1) {
        switch (opt) {
            case 'c': cfg = optarg; break;
            case 'r': rt = optarg; break;
            case 'i': inPath = optarg; break;
            case 'o': outPath = optarg; break;
            case 'd': break;
            default: std::cerr << "usage: router -c cfg -r routes [-i in] [-o out] [-d n]\n"; return 1;
        }
    }
    if (cfg.empty() || rt.empty()) { std::cerr << "-c and -r are required\n"; return 1; }

#ifdef BUG_HANG
    for (;;) sleep(1);
#endif

    std::vector<Iface> ifaces;
    std::ifstream cfgIn(cfg), rtIn(rt);
    for (const auto &line : contentLines(cfgIn)) {
        std::istringstream ls(line);
        Iface i; std::string cidr;
        ls >> i.name >> cidr;
        if (parseCidr(cidr, i.net)) ifaces.push_back(i);
    }
    std::vector<Route> routes;
    for (const auto &line : contentLines(rtIn)) {
        std::istringstream ls(line);
        std::string cidr, hop; Route r;
        ls >> cidr >> hop;
        if (parseCidr(cidr, r.net) && parseIp(hop, r.nextHop)) routes.push_back(r);
    }

    std::ifstream inFile;
    std::ofstream outFile;
    if (!inPath.empty()) inFile.open(inPath);
    if (!outPath.empty()) outFile.open(outPath);
    std::istream &in = inPath.empty() ? std::cin : inFile;
    std::ostream &out = outPath.empty() ? std::cout : outFile;

    for (const auto &dest : contentLines(in)) {
        uint32_t ip;
        if (!parseIp(dest, ip)) continue;
        const Iface *conn = egress(ifaces, ip);
        const Route *best = nullptr;
        for (const auto &r : routes) {
#ifdef BUG_FIRST_MATCH
            if (r.net.contains(ip) && !best) best = &r;
#else
            if (r.net.contains(ip) && (!best || r.net.len > best->net.len)) best = &r;
#endif
        }
        if (conn && (!best || conn->net.len >= best->net.len)) {
            out << dest << ": " << conn->name << " -> " << dest << "\n";
        } else if (best) {
            const Iface *e = egress(ifaces, best->nextHop);
            out << dest << ": " << (e ? e->name : "?") << " -> " << ipToString(best->nextHop) << "\n";
        } else {
            out << dest << ": unreachable\n";
        }
    }
    return 0;
}
