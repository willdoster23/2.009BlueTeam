#pragma once
#include <stdbool.h>
#include <stdint.h>

#define RING_SAMPLES 65536u
#define RING_MASK (RING_SAMPLES - 1u)
#define ADC_ERROR_BIT 0x8000u

typedef struct {
    uint32_t count, pre, threshold;
    int32_t baseline;
    uint64_t scanned, trigger, start, end;
    bool triggered, overwritten;
} capture_state;

static inline capture_state capture_init(uint32_t count, uint32_t pre,
                                         uint32_t threshold, int32_t baseline) {
    return (capture_state){.count=count, .pre=pre, .threshold=threshold, .baseline=baseline};
}

// total counts samples already written by DMA, not the next in-flight sample.
static inline bool capture_observe(capture_state *s, const uint16_t *ring, uint64_t total) {
    if (total < s->scanned || total - s->scanned > RING_SAMPLES) {
        s->overwritten=true;
        return false;
    }
    while (!s->triggered && s->scanned < total) {
        const uint64_t index=s->scanned++;
        const uint16_t raw=ring[index & RING_MASK];
        if (index < s->pre || (raw & ADC_ERROR_BIT)) continue;
        int32_t delta=(int32_t)(raw & 4095u) - s->baseline;
        if (delta < 0) delta=-delta;
        if ((uint32_t)delta >= s->threshold) {
            s->triggered=true;
            s->trigger=index;
            s->start=index-s->pre;
            s->end=s->start+s->count;
        }
    }
    s->scanned=total;
    if (s->triggered && total-s->start > RING_SAMPLES) s->overwritten=true;
    return s->triggered && !s->overwritten && total >= s->end;
}

static inline uint32_t crc32_byte(uint32_t crc, uint8_t byte) {
    crc ^= byte;
    for (unsigned bit=0;bit<8;++bit) crc=(crc>>1) ^ (0xedb88320u & (0u-(crc&1u)));
    return crc;
}
static inline uint32_t capture_crc(const uint16_t *ring, uint64_t start, uint32_t count) {
    uint32_t crc=0xffffffffu;
    for (uint32_t i=0;i<count;++i) {
        const uint16_t raw=ring[(start+i)&RING_MASK];
        crc=crc32_byte(crc,(uint8_t)raw);
        crc=crc32_byte(crc,(uint8_t)(raw>>8));
    }
    return crc ^ 0xffffffffu;
}
