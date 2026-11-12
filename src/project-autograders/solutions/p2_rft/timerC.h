//
// Created by Phillip Romig on 7/19/24.
//

#ifndef RFT_TIMERC_H
#define RFT_TIMERC_H
#include <chrono>


class timerC {
private:
    bool running_v;
    std::chrono::high_resolution_clock::time_point startTime_v;
    std::chrono::duration<float> duration_v;

public:
    timerC() : running_v(false), startTime_v(), duration_v(0) {};
   explicit timerC(int milliseconds) : running_v(false), startTime_v(),
        duration_v(std::chrono::milliseconds(milliseconds)) {};
    void setDuration(int milliseconds);
    void start();
    void stop();
    std::chrono::duration<float> getDuration() { return duration_v; };
    bool isRunning() { return running_v; };
    [[nodiscard]] bool timeout() const;
};


#endif //RFT_TIMERC_H
