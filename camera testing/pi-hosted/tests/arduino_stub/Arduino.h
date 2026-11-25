#pragma once
#include <algorithm>
#include <stdint.h>
#include <string>
#include <sstream>
using std::min;
constexpr uint8_t HIGH=1,LOW=0,OUTPUT=1,INPUT_PULLUP=2;
constexpr uint8_t PIN0_bm=1,PIN1_bm=2,TCB_CAPT_bm=1,TCB_CNTMODE_INT_gc=1,TCB_CLKSEL_CLKDIV1_gc=1,TCB_ENABLE_bm=1;
struct Ports{uint8_t IN=0,OUT=0;} VPORTA,VPORTB;
struct Timer{uint8_t CTRLA=0,CTRLB=0,INTFLAGS=0,INTCTRL=0;uint16_t CCMP=0,CNT=0;} TCB2;
uint32_t fakeMillis=0;
int pinLevels[32]={0};
void pinMode(uint8_t,uint8_t){}
void digitalWrite(uint8_t pin,uint8_t level){pinLevels[pin]=level;}
int digitalRead(uint8_t pin){return pinLevels[pin];}
void delayMicroseconds(unsigned){}
uint32_t millis(){return fakeMillis;}
struct SerialStub{
 std::string output;
 void begin(unsigned long){}
 int available(){return 0;}
 int read(){return -1;}
 template<class T>void print(T value){std::ostringstream s;s<<value;output+=s.str();}
 template<class T>void println(T value){print(value);output+='\n';}
 void write(const uint8_t* bytes,size_t length){output.append(reinterpret_cast<const char*>(bytes),length);}
}Serial;
