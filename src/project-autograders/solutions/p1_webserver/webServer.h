#ifndef HEADER_H
#define HEADER_H 

#include <iostream>
#include <fstream>
#include <regex>
#include <string>

#include <string.h>
#include <unistd.h>
#include <signal.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/socket.h>
#include <arpa/inet.h>

#include "logging.h"

#define GET 1
#define HEAD 2
#define POST 3

inline int BUFFER_SIZE = 10;

#endif
