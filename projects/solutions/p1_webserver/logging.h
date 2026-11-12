/*
    A simplistic set of macros to make creating debuging messages quick and easy.
    Author: Phil Romig
    Date: 2020-01-01

    Use as if it were an I/O Stream, replacing std::cout << with the message level
    you desire and ending the line with EDNL rather than std::endl
*/

#ifndef LOGGING_H
#define LOGGING_H

#include <iostream>
#include <filesystem>
#include <string>

#ifndef  __FILE_NAME__
#define __FILE_NAME__ std::filesystem::path(__FILE__).filename().string()
#endif

inline int LOG_LEVEL = 3;
#define TRACE   if (LOG_LEVEL > 5) { std::cerr << "TRACE: "
#define DEBUG   if (LOG_LEVEL > 4) { std::cerr << "DEBUG: "
#define INFO    if (LOG_LEVEL > 3) { std::cerr << "INFO: "
#define WARNING if (LOG_LEVEL > 2) { std::cerr << "WARNING: "
#define ERROR   if (LOG_LEVEL > 1) { std::cerr << "ERROR: "
#define FATAL   if (LOG_LEVEL > 0) { std::cerr << "FATAL: "
#define ENDL  " (" << __FILE_NAME__ << ":" << __LINE__ << ")" << std::endl; }


#endif //LOGGING_H