"""Pico 2 MicroPython: AS5048A pulse input on GP2, continuous USB serial position."""
import os
import sys
import time

from machine import Pin
import rp2


@rp2.asm_pio(in_shiftdir=rp2.PIO.SHIFT_LEFT, fifo_join=rp2.PIO.JOIN_RX)
def measure():
    wait(0, pin, 0)
    wait(1, pin, 0)
    wrap_target()
    mov(isr, null)
    mov(x, invert(null))
    label("high")
    jmp(pin, "high_tick")
    jmp("high_end")
    label("high_tick")
    jmp(x_dec, "high")
    label("high_end")
    in_(x, 16)
    mov(x, invert(null))
    label("low")
    jmp(pin, "low_end")
    jmp(x_dec, "low")
    label("low_end")
    in_(x, 16)
    push(noblock)
    wrap()


pwm = Pin(2, Pin.IN, Pin.PULL_DOWN)
reader = rp2.StateMachine(0, measure, freq=8000000, in_base=pwm, jmp_pin=pwm)
reader.active(1)

# The encoder's PWM frame is 4119 clocks. Its valid zero starts with 16 high clocks.
# Firmware reports accumulated 12-bit angle counts, or 4096 counts per revolution.
period_counts = 4096
epoch = int.from_bytes(os.urandom(4), "little")
sequence = 0
position = 0
angle = None
last_frame = last_loop = last_report = time.ticks_ms()

while True:
    now = time.ticks_ms()
    if angle is not None and (time.ticks_diff(now, last_frame) > 20
                              or time.ticks_diff(now, last_loop) > 20):
        angle = None
        position = 0
        epoch = (epoch + 1) & 0xffffffff
        reader.restart()
        while reader.rx_fifo():
            reader.get()
    last_loop = now
    while reader.rx_fifo():
        word = reader.get()
        # Each loop counts two PIO cycles. Include the cycles spent switching loops.
        high = 65535 - (word >> 16) + 3
        low = 65535 - (word & 65535) + 2
        total = high + low
        if not 3150 <= total <= 5100:
            continue
        value = round(4119 * high / total) - 16
        if not -2 <= value < period_counts + 2:
            continue
        current = min(period_counts - 1, max(0, value))
        if angle is not None:
            delta = (current - angle + period_counts // 2) % period_counts - period_counts // 2
            position += delta
        angle = current
        last_frame = now

    if time.ticks_diff(now, last_report) >= 50:
        sequence = (sequence + 1) & 0xffffffff
        state = "idle" if angle is not None else "signal"
        sys.stdout.write("BM1 Y {} {} {} {}\n".format(sequence, position, state, epoch))
        last_report = now
    time.sleep_ms(1)
