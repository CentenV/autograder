//
// Created by Phillip Romig on 7/19/24.
//

#include "timerC.h"
#include <stdexcept>

void timerC::setDuration(int milliseconds) {
    if (running_v)
        throw std::runtime_error("Cannot set duration while timer is running");
    duration_v = std::chrono::milliseconds(milliseconds);
}

void timerC::start() {
    startTime_v = std::chrono::high_resolution_clock::now();
    running_v = true;
}

void timerC::stop() {
    running_v = false;
}


bool timerC::timeout() const {
    if (running_v) {
        auto currentTime = std::chrono::high_resolution_clock::now();
        auto elapsedTime = std::chrono::duration_cast<std::chrono::milliseconds>(currentTime - startTime_v);
        return elapsedTime >= duration_v;
    }
    return false;
}
