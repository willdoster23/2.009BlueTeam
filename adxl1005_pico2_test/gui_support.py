"""Device selection and input validation shared by the standalone desktop UI."""
from dataclasses import dataclass
from pathlib import Path
import math
import capture


@dataclass(frozen=True)
class Device:
    port: str
    description: str
    vid: int | None
    pid: int | None
    serial: str | None
    pico: bool

    @property
    def key(self):
        return (self.port,self.vid,self.pid,self.serial)

    @property
    def label(self):
        serial=f' · {self.serial[-8:]}' if self.serial else ''
        name='Pico' if self.pico else self.description[:24]
        return f'{name}{serial} · {Path(self.port).name.removeprefix("cu.")}'


def devices(show_all=False,ports=None):
    if ports is None:
        from serial.tools import list_ports
        ports=list_ports.comports()
    found={}
    for port in ports:
        description=getattr(port,'product',None) or port.description or 'USB serial device'
        pico=port.vid==0x2e8a or 'pico' in description.lower()
        if show_all or pico:
            found[port.device]=Device(port.device,description,port.vid,port.pid,port.serial_number,pico)
    return sorted(found.values(),key=lambda item:(not item.pico,item.port))


def settings(values):
    labels={'mass':'moving mass','threshold':'trigger level','window':'recording window',
            'pre':'pretrigger time','timeout':'wait time','supply':'sensor supply','reference':'ADC reference'}
    parsed={}
    for name,label in labels.items():
        try: value=float(values[name])
        except (KeyError,TypeError,ValueError): raise ValueError(f'Enter a number for {label}.') from None
        if not math.isfinite(value): raise ValueError(f'Enter a finite number for {label}.')
        parsed[name]=value
    if not 0<parsed['mass']<=100000:
        raise ValueError('Enter a positive moving mass in grams to apply the 3 N tap minimum.')
    if not 3<=parsed['supply']<=3.4 or not 3<=parsed['reference']<=3.4:
        raise ValueError('This wiring uses 3.3 V. Supply and reference must be between 3.0 and 3.4 V.')
    sensitivity_text=str(values.get('sensitivity','')).strip()
    try: sensitivity=float(sensitivity_text) if sensitivity_text else None
    except ValueError: raise ValueError('Enter a valid sensitivity in mV/g, or leave it blank.') from None
    if sensitivity is not None and (not math.isfinite(sensitivity) or not 1<=sensitivity<=100):
        raise ValueError('Sensitivity must be between 1 and 100 mV/g.')
    scale=sensitivity if sensitivity is not None else 20*parsed['supply']/5
    count=round(parsed['window']*capture.RATE_HZ/1000)
    pre=round(parsed['pre']*capture.RATE_HZ/1000)
    threshold=round(parsed['threshold']*scale/1000/parsed['reference']*4095)
    if not 1000<=count<=60000: raise ValueError('Recording window must be between 4 and 240 ms.')
    if not 0<=pre<count: raise ValueError('Pretrigger time must be 0 or greater and shorter than the recording window.')
    if not 1<=threshold<=2047: raise ValueError('Choose a positive trigger level within the sensor range.')
    if not .1<=parsed['timeout']<=60: raise ValueError('Wait time must be between 0.1 and 60 seconds.')
    try: period=float(values.get('period',30))
    except (ValueError,TypeError): raise ValueError('Enter a recording period in seconds.') from None
    if not math.isfinite(period) or not 1<=period<=120: raise ValueError('Recording period must be between 1 and 120 seconds.')
    try: cooldown=float(values.get('cooldown',2))
    except (ValueError,TypeError): raise ValueError('Enter a pause after each capture, in seconds.') from None
    if not math.isfinite(cooldown) or not 0<=cooldown<=10:
        raise ValueError('Pause after each capture must be between 0 and 10 seconds.')
    return dict(mass_g=parsed['mass'],threshold_counts=threshold,count=count,pre=pre,
                timeout_s=parsed['timeout'],supply_v=parsed['supply'],
                adc_reference_v=parsed['reference'],sensitivity_mv_g=sensitivity,period_s=period,
                cooldown_s=cooldown)
