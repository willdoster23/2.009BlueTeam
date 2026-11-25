// Passive ADXL1005 recorder. No solenoid, motor, relay or driver outputs.
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "pico/stdlib.h"
#include "pico/stdio_usb.h"
#include "hardware/adc.h"
#include "hardware/clocks.h"
#include "hardware/dma.h"
#include "hardware/irq.h"
#include "hardware/sync.h"
#include "capture_core.h"

#if !PICO_RP2350 || !defined(PICO_DEFAULT_LED_PIN)
#error Build for PICO_BOARD=pico2 (RP2350, not the original Pico or Pico 2 W).
#endif

#define SAMPLE_RATE 250000u
#define MAX_CAPTURE 60000u
#define BASELINE_SAMPLES 8192u
#define ADC_PIN 26u
#define OVERRANGE_PIN 15u
// Hardware address-wrap fields are too small for this whole capture. Two
// chained DMA channels fill alternate halves, rearming the idle channel in IRQ.
// Sampling never waits for the CPU to copy a block or restart the ADC.
#define BLOCK_SAMPLES (RING_SAMPLES/2u)
static uint16_t samples[RING_SAMPLES];
static int adc_dma, alternate_dma;
static volatile uint32_t completed_samples;
static volatile bool ring_running, dma_stalled;
static uint64_t frozen_total;
static volatile bool overrange_seen;
static float actual_rate;
static double baseline_mean, baseline_noise;

static void identity(void) {
    puts("AXL1 HELLO version=1 board=pico2 adc=26 rate_hz=250000 max_samples=60000");
}
static void overrange_irq(uint gpio, uint32_t events) {
    if (gpio==OVERRANGE_PIN && (events & GPIO_IRQ_EDGE_RISE)) overrange_seen=true;
}
static uint32_t dma_mask(void) {return (1u<<adc_dma)|(1u<<alternate_dma);}
static void dma_irq(void) {
    const uint32_t pending=dma_hw->ints0 & dma_mask();
    dma_hw->ints0=pending;
    if (!ring_running) return;
    const bool second=(completed_samples/BLOCK_SAMPLES)&1u;
    const int finished=second ? alternate_dma:adc_dma;
    if (pending != (1u<<finished)) {dma_stalled=true;return;}
    completed_samples+=BLOCK_SAMPLES;
    dma_channel_set_write_addr(finished,samples+(second ? BLOCK_SAMPLES:0),false);
    dma_channel_set_trans_count(finished,dma_encode_transfer_count(BLOCK_SAMPLES),false);
}
static uint64_t ring_total(void) {
    uint32_t before,after,remaining;
    do {
        before=completed_samples;
        const int active=((before/BLOCK_SAMPLES)&1u) ? alternate_dma:adc_dma;
        remaining=dma_channel_hw_addr(active)->transfer_count & DMA_CH0_TRANS_COUNT_COUNT_BITS;
        after=completed_samples;
    } while (before!=after);
    __dmb();
    return (uint64_t)before+BLOCK_SAMPLES-remaining;
}
static void stop_adc(void) {
    dma_channel_set_irq0_enabled(adc_dma,false);
    dma_channel_set_irq0_enabled(alternate_dma,false);
    adc_run(false);
    while (!(adc_hw->cs & ADC_CS_READY_BITS)) tight_loop_contents();
    if (ring_running) frozen_total=ring_total();
    ring_running=false;
    dma_channel_abort(adc_dma);
    dma_channel_abort(alternate_dma);
    dma_hw->ints0=dma_mask();
}
static void start_adc(bool circular, uint32_t count) {
    stop_adc();
    adc_fifo_drain();
    adc_hw->fcs |= ADC_FCS_OVER_BITS | ADC_FCS_UNDER_BITS;
    dma_channel_config config=dma_channel_get_default_config(adc_dma);
    channel_config_set_transfer_data_size(&config,DMA_SIZE_16);
    channel_config_set_read_increment(&config,false);
    channel_config_set_write_increment(&config,true);
    channel_config_set_dreq(&config,DREQ_ADC);
    channel_config_set_high_priority(&config,true);
    if (circular) {
        completed_samples=0;dma_stalled=false;ring_running=true;
        channel_config_set_chain_to(&config,alternate_dma);
        dma_channel_configure(adc_dma,&config,samples,&adc_hw->fifo,dma_encode_transfer_count(BLOCK_SAMPLES),false);
        channel_config_set_chain_to(&config,adc_dma);
        dma_channel_configure(alternate_dma,&config,samples+BLOCK_SAMPLES,&adc_hw->fifo,dma_encode_transfer_count(BLOCK_SAMPLES),false);
        dma_channel_set_irq0_enabled(adc_dma,true);
        dma_channel_set_irq0_enabled(alternate_dma,true);
        dma_start_channel_mask(1u<<adc_dma);
    } else {
        dma_channel_configure(adc_dma,&config,samples,&adc_hw->fifo,dma_encode_transfer_count(count),true);
    }
    adc_run(true);
}
static bool baseline(void) {
    start_adc(false,BASELINE_SAMPLES);
    dma_channel_wait_for_finish_blocking(adc_dma);
    stop_adc();
    if (adc_hw->fcs & ADC_FCS_OVER_BITS) return false;
    double sum=0, sum_sq=0;
    for (uint32_t i=0;i<BASELINE_SAMPLES;++i) {
        if (samples[i] & ADC_ERROR_BIT) return false;
        const double value=samples[i]&4095u;
        sum+=value; sum_sq+=value*value;
    }
    baseline_mean=sum/BASELINE_SAMPLES;
    baseline_noise=sqrt(fmax(0,sum_sq/BASELINE_SAMPLES-baseline_mean*baseline_mean));
    return baseline_mean>250 && baseline_mean<3850;
}
static void arm(uint32_t threshold, uint32_t count, uint32_t pre, uint32_t timeout_ms) {
    if (!threshold || threshold>2047 || count<1000 || count>MAX_CAPTURE || pre>=count || timeout_ms<100 || timeout_ms>60000) {
        puts("AXL1 ERROR invalid_arm_parameters"); return;
    }
    puts("AXL1 BASELINE hold_the_tip_still");
    if (!baseline()) {puts("AXL1 ERROR baseline_invalid_check_power_wiring_and_motion"); return;}
    capture_state capture=capture_init(count,pre,threshold,(int32_t)lround(baseline_mean));
    printf("AXL1 READY {\"rate_hz\":%.6f,\"threshold_counts\":%lu,\"baseline_counts\":%.6f}\n",
           (double)actual_rate,(unsigned long)threshold,baseline_mean);
    stdio_flush();
    overrange_seen=gpio_get(OVERRANGE_PIN);
    gpio_put(PICO_DEFAULT_LED_PIN,1);
    start_adc(true,0);
    uint64_t total=0, started=time_us_64(), previous_poll=started, control_poll=started;
    const char *error=NULL;
    while (true) {
        const uint64_t now=time_us_64();
        // Prevent an unobserved complete ring lap from producing false timing.
        if (now-previous_poll > (uint64_t)(500000.0*RING_SAMPLES/actual_rate)) {error="capture_poll_stalled";break;}
        previous_poll=now;
        total=ring_total();
        const bool done=capture_observe(&capture,samples,total);
        if (dma_stalled) {error="dma_block_rearm_missed";break;}
        if (capture.overwritten) {error="capture_window_overwritten";break;}
        if (adc_hw->fcs & ADC_FCS_OVER_BITS) {error="adc_fifo_overflow";break;}
        if (done) break;
        if (!capture.triggered && now-started>(uint64_t)timeout_ms*1000) {error="trigger_timeout";break;}
        if (now-control_poll>=1000) {
            control_poll=now;
            if (!stdio_usb_connected() || getchar_timeout_us(0)=='!') {error="cancelled";break;}
        }
        tight_loop_contents();
    }
    stop_adc();
    const bool capture_overrange=overrange_seen;
    total=frozen_total;
    gpio_put(PICO_DEFAULT_LED_PIN,0);
    if (!error && time_us_64()-previous_poll>(uint64_t)(500000.0*RING_SAMPLES/actual_rate)) error="capture_poll_stalled";
    if (!error && dma_stalled) error="dma_block_rearm_missed";
    if (!error && (adc_hw->fcs & ADC_FCS_OVER_BITS)) error="adc_fifo_overflow";
    if (!error && capture.triggered && total-capture.start>RING_SAMPLES) error="capture_window_overwritten";
    if (error) {printf("AXL1 ERROR %s\n",error);return;}
    const uint32_t crc=capture_crc(samples,capture.start,count);
    printf("AXL1 DATA {\"schema\":1,\"rate_hz\":%.6f,\"sample_count\":%lu,\"trigger_index\":%lu,"
           "\"baseline_counts\":%.8f,\"baseline_noise_counts\":%.8f,\"crc32\":%lu,"
           "\"overrange_seen\":%s,\"adc_pin\":26,\"or_pin\":15}\n",
           (double)actual_rate,(unsigned long)count,(unsigned long)pre,baseline_mean,baseline_noise,
           (unsigned long)crc,capture_overrange ? "true":"false");
    // Send only after acquisition stops. Binary words are native little-endian.
    uint32_t remaining=count, index=(uint32_t)capture.start&RING_MASK;
    while (remaining && stdio_usb_connected()) {
        uint32_t chunk=remaining<256 ? remaining:256;
        if (chunk>RING_SAMPLES-index) chunk=RING_SAMPLES-index;
        stdio_put_string((const char*)&samples[index],(int)(chunk*sizeof(uint16_t)),false,false);
        index=(index+chunk)&RING_MASK;
        remaining-=chunk;
    }
    puts("\nAXL1 END");
    stdio_flush();
}
int main(void) {
    stdio_init_all();
    stdio_set_translate_crlf(&stdio_usb,false);
    gpio_init(PICO_DEFAULT_LED_PIN);gpio_set_dir(PICO_DEFAULT_LED_PIN,GPIO_OUT);
    gpio_init(OVERRANGE_PIN);gpio_set_dir(OVERRANGE_PIN,GPIO_IN);gpio_pull_down(OVERRANGE_PIN);
    gpio_set_irq_enabled_with_callback(OVERRANGE_PIN,GPIO_IRQ_EDGE_RISE,true,overrange_irq);
    adc_init();adc_gpio_init(ADC_PIN);adc_select_input(0);
    adc_fifo_setup(true,true,1,true,false);
    const uint32_t adc_clock=clock_get_hz(clk_adc);
    const float divisor=(float)adc_clock/SAMPLE_RATE-1.0f;
    adc_set_clkdiv(divisor);
    actual_rate=(float)adc_clock/fmaxf(96.0f,divisor+1.0f);
    adc_dma=dma_claim_unused_channel(true);
    alternate_dma=dma_claim_unused_channel(true);
    irq_set_exclusive_handler(DMA_IRQ_0,dma_irq);
    irq_set_enabled(DMA_IRQ_0,true);
    char command[100];size_t length=0;bool discard=false;
    while (true) {
        const int ch=getchar_timeout_us(10000);
        if (ch==PICO_ERROR_TIMEOUT) continue;
        if (ch=='\r') continue;
        if (ch=='\n') {
            if (discard) puts("AXL1 ERROR command_too_long");
            else {
                command[length]=0;
                unsigned long threshold,count,pre,timeout_ms;char extra;
                if (!strcmp(command,"HELLO")) identity();
                else if (sscanf(command,"ARM %lu %lu %lu %lu %c",&threshold,&count,&pre,&timeout_ms,&extra)==4 &&
                         threshold<=2047 && count<=MAX_CAPTURE && pre<=MAX_CAPTURE && timeout_ms<=60000)
                    arm((uint32_t)threshold,(uint32_t)count,(uint32_t)pre,(uint32_t)timeout_ms);
                else puts("AXL1 ERROR unknown_command");
            }
            length=0;discard=false;
        } else if (ch!='!' && !discard) {
            if (length<sizeof(command)-1) command[length++]=(char)ch;
            else discard=true;
        }
    }
}
