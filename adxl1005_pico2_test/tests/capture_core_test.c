#include <assert.h>
#include <stdio.h>
#include "../firmware/capture_core.h"
static uint16_t ring[RING_SAMPLES];
int main(void) {
    capture_state s=capture_init(1000,100,100,2048);
    for (uint64_t i=0;i<71000;++i) {
        ring[i&RING_MASK]=i==70000 ? 2200:2048;
        capture_observe(&s,ring,i+1);
    }
    assert(s.triggered && !s.overwritten && s.trigger==70000);
    assert(s.start==69900 && s.end==70900);
    assert(ring[(s.start+100)&RING_MASK]==2200);
    s=capture_init(1000,100,100,2048);
    for (unsigned i=0;i<200;++i) ring[i]=2048;
    ring[50]=2200; // Too early: insufficient pretrigger samples.
    ring[110]=0x8fff; // Invalid conversion must not trigger.
    ring[150]=1900; // Negative acceleration triggers as well.
    assert(!capture_observe(&s,ring,200));
    assert(s.triggered && s.trigger==150 && s.start==50);
    capture_observe(&s,ring,s.start+RING_SAMPLES+1);
    assert(s.overwritten);
    s=capture_init(1000,100,100,2048);
    capture_observe(&s,ring,RING_SAMPLES+1);
    assert(s.overwritten);
    uint32_t crc=0xffffffffu;
    const char *known="123456789";
    for (unsigned i=0;i<9;++i) crc=crc32_byte(crc,(uint8_t)known[i]);
    assert((crc^0xffffffffu)==0xcbf43926u);
    ring[RING_MASK]=0x0201;ring[0]=0x0403;
    assert(capture_crc(ring,RING_MASK,2)==0xb63cfbcdu);
    puts("Capture core passed: trigger, pretrigger, ring wrapping, ADC errors, overwrite detection and CRC32.");
}
