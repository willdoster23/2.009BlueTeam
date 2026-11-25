#!/usr/bin/env python3
"""Standalone Pico 2 ADXL1005 recorder. Never controls the tapper or main UI."""
import argparse
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import struct
import sys
import threading
import time
import zlib

RATE_HZ=250000
G0=9.80665
DEFAULT_MASS_G=73.0
PLOT_LOCK=threading.RLock()
FORCE_NOTE=('Mass times baseline-removed axial acceleration: an inertial-force estimate, '
            'not measured wall/contact force. Coil, spring, friction and gravity forces are not separated.')


def validate_capture(meta, raw):
    count=meta.get('sample_count')
    trigger=meta.get('trigger_index')
    rate=meta.get('rate_hz')
    if (meta.get('schema')!=1 or type(count) is not int or not 1000<=count<=60000 or
            type(trigger) is not int or not 0<=trigger<count or
            type(rate) not in (int,float) or not math.isfinite(rate) or not 249000<=rate<=251000):
        raise ValueError('Unsupported or invalid capture header.')
    if len(raw)!=2*count:
        raise ValueError('Capture byte count does not match its header.')
    if zlib.crc32(raw)!=meta.get('crc32'):
        raise ValueError('Capture checksum failed; no measurement files were accepted.')
    for name in ('baseline_counts','baseline_noise_counts'):
        if type(meta.get(name)) not in (int,float) or not math.isfinite(meta[name]):
            raise ValueError('Invalid baseline calibration in capture header.')
    if not 0<=meta['baseline_counts']<=4095 or meta['baseline_noise_counts']<0:
        raise ValueError('Invalid baseline calibration in capture header.')
    return [item[0] for item in struct.iter_unpack('<H',raw)]


def convert(meta, raw, mass_g=DEFAULT_MASS_G, supply_v=3.3, adc_reference_v=3.3,
            sensitivity_mv_g=None):
    if not math.isfinite(mass_g) or not 0<=mass_g<=100000:
        raise ValueError('Use a nonnegative moving mass in grams, or 0 to omit force.')
    if not all(math.isfinite(v) and 3.0<=v<=3.4 for v in (supply_v,adc_reference_v)):
        raise ValueError('This wiring is for a 3.3 V sensor and Pico ADC; enter measured values from 3.0 to 3.4 V.')
    sensitivity_mv_g=20*supply_v/5 if sensitivity_mv_g is None else sensitivity_mv_g
    if not math.isfinite(sensitivity_mv_g) or not 1<=sensitivity_mv_g<=100:
        raise ValueError('Check sensitivity in millivolts per g.')
    words=validate_capture(meta,raw)
    volts_per_count=adc_reference_v/4095
    sensitivity_v_g=sensitivity_mv_g/1000
    baseline_v=meta['baseline_counts']*volts_per_count
    rows=[]
    for i,word in enumerate(words):
        counts=word&4095
        volts=counts*volts_per_count
        error=bool(word&0x8000)
        accel_g=(volts-baseline_v)/sensitivity_v_g if not error else None
        accel_m_s2=accel_g*G0 if accel_g is not None else None
        # The electrical midscale check also includes constant gravity and bias.
        range_warning=abs((volts-supply_v/2)/sensitivity_v_g)>=95
        rows.append(dict(time_s=(i-meta['trigger_index'])/meta['rate_hz'],adc_counts=counts,
                         adc_voltage_v=volts,acceleration_g=accel_g,acceleration_m_s2=accel_m_s2,
                         inertial_force_estimate_N=mass_g/1000*accel_m_s2 if mass_g and accel_m_s2 is not None else None,
                         adc_error=int(error),adc_rail_warning=int(counts<=4 or counts>=4091),
                         sensor_range_warning=int(range_warning)))
    valid=[row for row in rows if not row['adc_error']]
    if not valid:
        raise ValueError('Every ADC conversion was marked invalid.')
    warnings=[]
    if any(row['adc_error'] for row in rows): warnings.append('ADC conversion errors: those samples have blank acceleration/force.')
    if any(row['adc_rail_warning'] for row in rows): warnings.append('ADC voltage approached a rail; observed peaks may be clipped.')
    if any(row['sensor_range_warning'] for row in rows): warnings.append('Sensor approached/exceeded its nominal +/-100 g range; true impact peak may be higher.')
    if meta.get('overrange_seen'): warnings.append('OR input asserted during the armed recording; the sensor may have undergone overload recovery.')
    if not meta.get('or_wired'): warnings.append('OR monitoring was not declared wired; absence of OR does not establish a valid impact peak.')
    noise_g=meta['baseline_noise_counts']*volts_per_count/sensitivity_v_g
    if noise_g>1: warnings.append('Large stationary baseline noise; check mounting, wiring and electrical interference.')
    peak=max(abs(row['acceleration_g']) for row in valid)
    summary=dict(observed_peak_abs_acceleration_g=peak,
                 observed_peak_abs_inertial_force_estimate_N=mass_g/1000*peak*G0 if mass_g else None,
                 force_is_contact_force=False,force_note=FORCE_NOTE,moving_mass_g=mass_g or None,
                 baseline_noise_g_rms=noise_g,baseline_voltage_v=baseline_v,
                 sensitivity_mv_per_g=sensitivity_mv_g,sensor_supply_v=supply_v,
                 adc_reference_v=adc_reference_v,software_warnings=warnings,
                 adc_error_samples=sum(row['adc_error'] for row in rows),
                 sensor_range_warning_samples=sum(row['sensor_range_warning'] for row in rows),
                 sample_rate_hz=meta['rate_hz'],sample_count=len(rows),
                 pretrigger_ms=meta['trigger_index']/meta['rate_hz']*1000)
    return rows,summary


class CaptureCancelled(RuntimeError):
    pass


def check_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise CaptureCancelled('Recording cancelled.')


def read_exact(link,count,timeout_s=15,cancel_event=None):
    result=bytearray()
    deadline=time.monotonic()+timeout_s
    while len(result)<count:
        check_cancel(cancel_event)
        block=link.read(count-len(result))
        if block: result.extend(block)
        if time.monotonic()>deadline: raise TimeoutError('USB capture transfer timed out.')
    return bytes(result)


def read_line(link,deadline,limit=2048,cancel_event=None):
    """Accumulate header fragments across serial timeouts without losing bytes."""
    line=bytearray()
    while time.monotonic()<deadline:
        check_cancel(cancel_event)
        part=link.readline(limit-len(line))
        if part:
            line.extend(part)
            if line.endswith(b'\n'): return bytes(line)
        if len(line)>=limit: raise ValueError('Oversized recorder header.')
    raise TimeoutError('Timed out waiting for a recorder header.')


def identify_link(link,cancel_event=None):
    link.reset_input_buffer()
    link.write(b'HELLO\n')
    deadline=time.monotonic()+3
    try:
        while time.monotonic()<deadline:
            line=read_line(link,deadline,1024,cancel_event).decode('ascii',errors='replace').strip()
            if line.startswith('AXL1 HELLO version=1 board=pico2 '): return
    except TimeoutError:
        pass
    raise RuntimeError('The selected port did not identify as the standalone Pico 2 ADXL recorder. No ARM request was sent. Load the included test UF2 first.')


def probe(port,cancel_event=None):
    """Verify the selected device without arming or keeping its serial port locked."""
    import serial
    with serial.Serial(port,115200,timeout=.2,write_timeout=2) as link:
        identify_link(link,cancel_event)


def record(port,threshold_counts,count,pre,timeout_s,progress=None,cancel_event=None):
    import serial
    def report(phase,message):
        if progress: progress(phase,message)
        else: print(message,flush=True)
    with serial.Serial(port,115200,timeout=.2,write_timeout=2) as link:
        identify_link(link,cancel_event)
        report('baseline','Hold the tip still for the short baseline measurement.')
        try:
            check_cancel(cancel_event)
            link.write(f'ARM {threshold_counts} {count} {pre} {round(timeout_s*1000)}\n'.encode('ascii'))
            deadline=time.monotonic()+timeout_s+5
            while time.monotonic()<deadline:
                line=read_line(link,deadline,cancel_event=cancel_event)
                if line.startswith(b'AXL1 READY '):
                    info=json.loads(line[len(b'AXL1 READY '):])
                    report('armed',f"ARMED: tap once now (threshold {info['threshold_counts']} ADC counts).")
                elif line.startswith(b'AXL1 ERROR '):
                    raise RuntimeError(line.decode('ascii',errors='replace').strip())
                elif line.startswith(b'AXL1 DATA '):
                    meta=json.loads(line[len(b'AXL1 DATA '):])
                    count=meta.get('sample_count')
                    if type(count) is not int or not 1000<=count<=60000:
                        raise ValueError('Unsafe/invalid capture length in header.')
                    report('transfer','Tap captured. Receiving the recorded trace…')
                    raw=read_exact(link,2*count,cancel_event=cancel_event)
                    if read_exact(link,len(b'\nAXL1 END\n'),cancel_event=cancel_event)!=b'\nAXL1 END\n':
                        raise ValueError('Capture framing error.')
                    validate_capture(meta,raw)
                    return meta,raw
            raise TimeoutError('The Pico did not return a complete capture.')
        except BaseException:
            try: link.write(b'!')
            except (OSError,serial.SerialException): pass
            raise


def demo(count,pre,or_wired=False):
    """Synthetic waveform only; never opens a serial port."""
    rng=random.Random(1005)
    words=[]
    for i in range(count):
        t=(i-pre)/RATE_HZ
        acceleration=(28*math.exp(-((t-.001)/.00025)**2) +
                      (4*math.sin(2*math.pi*1500*t)*math.exp(-t/.006) if t>=0 else 0))
        counts=round(2048+acceleration*(4095*.020/5)+rng.gauss(0,.6))
        words.append(max(0,min(4095,counts)))
    raw=struct.pack('<'+'H'*count,*words)
    return dict(schema=1,rate_hz=RATE_HZ,sample_count=count,trigger_index=pre,
                baseline_counts=2048.,baseline_noise_counts=.6,crc32=zlib.crc32(raw),
                overrange_seen=False,or_wired=or_wired,adc_pin=26,or_pin=15,simulated=True),raw


def draw_plot(figure,rows,meta,mass_g):
    with PLOT_LOCK:
        figure.clear()
        axes=figure.subplots(2,1,sharex=True)
        x=[row['time_s']*1000 for row in rows]
        axes[0].plot(x,[row['acceleration_g'] for row in rows],color='#126b9b',lw=.8)
        axes[0].set_ylabel('Axial acceleration (g)\nstationary baseline removed')
        if mass_g:
            axes[1].plot(x,[row['inertial_force_estimate_N'] for row in rows],color='#a65a13',lw=.8)
            axes[1].set_ylabel(f'Inertial-force estimate (N)\nmoving mass {mass_g:g} g')
        else:
            axes[1].text(.5,.5,'Force omitted: enter total moving mass with --mass-g.',ha='center',transform=axes[1].transAxes)
            axes[1].set_ylabel('Force unavailable')
        for ax in axes:
            ax.axvline(0,color='#777777',lw=.7,ls='--');ax.axhline(0,color='#777777',lw=.5)
            ax.grid(alpha=.15)
        axes[1].set_xlabel('Time from acceleration trigger (ms)')
        title=('SIMULATED EXAMPLE — ' if meta.get('simulated') else '')+'ADXL1005 solenoid-tapper test'
        figure.suptitle(title)
        note='Force estimate is m×a, not measured contact force. Raw sampled trace; no digital filtering.'
        if meta.get('overrange_seen') or any(row['sensor_range_warning'] or row['adc_rail_warning'] for row in rows):
            note='RANGE / OVERLOAD WARNING — impact peak may be clipped. '+note
        figure.supxlabel(note,fontsize=8)


def save_capture(meta,raw,output,mass_g,supply_v,adc_reference_v,sensitivity_mv_g,quiet=False,return_data=False):
    rows,summary=convert(meta,raw,mass_g,supply_v,adc_reference_v,sensitivity_mv_g)
    output=Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('Output folder is not empty; choose a fresh folder to preserve prior captures.')
    output.mkdir(parents=True,exist_ok=True)
    (output/'raw_adc_u16le.bin').write_bytes(raw)
    (output/'capture.json').write_text(json.dumps(dict(capturedAt=datetime.now(timezone.utc).isoformat(),
        capture=meta,analysis=summary),indent=2)+'\n')
    with (output/'samples.csv').open('w',newline='') as file:
        writer=csv.DictWriter(file,fieldnames=list(rows[0]))
        writer.writeheader();writer.writerows(rows)
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    with PLOT_LOCK:
        figure=Figure(figsize=(11,7),layout='constrained')
        FigureCanvasAgg(figure)
        draw_plot(figure,rows,meta,mass_g)
        figure.savefig(output/'acceleration_and_inertial_force.png',dpi=160)
        figure.clear()
    if not quiet:
        print(f'Saved {output.resolve()}')
        print(f"Observed peak: {summary['observed_peak_abs_acceleration_g']:.3f} g")
        if mass_g: print(f"Observed inertial-force estimate: {summary['observed_peak_abs_inertial_force_estimate_N']:.3f} N (not contact force)")
        for warning in summary['software_warnings']: print('Note: '+warning)
    return (output,rows,summary) if return_data else output


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--port',help='Pico test-recorder USB serial device; never selected automatically')
    mode.add_argument('--demo',action='store_true',help='Generate a clearly marked synthetic example; no hardware')
    mode.add_argument('--reprocess',type=Path,help='Existing capture.json: recalculate with a new mass/calibration')
    mode.add_argument('--list',action='store_true',help='List USB serial ports without connecting')
    parser.add_argument('--mass-g',type=float,default=DEFAULT_MASS_G,help='Total moving mass in grams, default 73; 0 omits force')
    parser.add_argument('--threshold-g',type=float,default=2,help='Trigger on baseline-removed axial acceleration, default 2 g')
    parser.add_argument('--window-ms',type=float,default=200,help='Capture length, 4 to 240 ms; default 200')
    parser.add_argument('--pre-ms',type=float,default=20,help='Pretrigger portion, default 20 ms')
    parser.add_argument('--timeout',type=float,default=30,help='Seconds to wait for a tap, default 30')
    parser.add_argument('--or-wired',action='store_true',help='Record that ORout is connected to GP15')
    parser.add_argument('--supply-v',type=float,default=3.3)
    parser.add_argument('--adc-reference-v',type=float,default=3.3)
    parser.add_argument('--sensitivity-mv-g',type=float,help='Measured sensitivity; default 20 mV/g × supply/5 V')
    parser.add_argument('--output',type=Path,help='New empty output folder')
    args=parser.parse_args()
    if args.list:
        from serial.tools import list_ports
        for port in list_ports.comports(): print(f'{port.device}\t{port.description}\t{port.serial_number or ""}')
        return
    if not (args.port or args.demo or args.reprocess): parser.error('Choose --port, --demo, --reprocess, or --list.')
    numeric=[args.mass_g,args.threshold_g,args.window_ms,args.pre_ms,args.timeout,args.supply_v,args.adc_reference_v]
    if not all(math.isfinite(value) for value in numeric): parser.error('Use finite numbers.')
    if not 0<=args.mass_g<=100000 or not 3<=args.supply_v<=3.4 or not 3<=args.adc_reference_v<=3.4:
        parser.error('Check mass and 3.3 V supply/reference values.')
    count=round(args.window_ms*RATE_HZ/1000);pre=round(args.pre_ms*RATE_HZ/1000)
    sensitivity=args.sensitivity_mv_g if args.sensitivity_mv_g is not None else 20*args.supply_v/5
    if not math.isfinite(sensitivity) or not 1<=sensitivity<=100: parser.error('Check sensitivity.')
    threshold=round(args.threshold_g*sensitivity/1000/args.adc_reference_v*4095)
    if not 1000<=count<=60000 or not 0<=pre<count or not 1<=threshold<=2047 or not .1<=args.timeout<=60:
        parser.error('Check trigger threshold, window/pretrigger length, and timeout.')
    output=args.output or Path(__file__).parent/'captures'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    if args.reprocess:
        stored=json.loads(args.reprocess.read_text());meta=stored['capture']
        raw=(args.reprocess.parent/'raw_adc_u16le.bin').read_bytes()
    elif args.demo:
        meta,raw=demo(count,pre,args.or_wired)
    else:
        meta,raw=record(args.port,threshold,count,pre,args.timeout)
        meta['or_wired']=args.or_wired
    save_capture(meta,raw,output,args.mass_g,args.supply_v,args.adc_reference_v,args.sensitivity_mv_g)


if __name__=='__main__':
    try: main()
    except KeyboardInterrupt:
        print('\nCapture cancelled. This recorder controls no actuators.',file=sys.stderr);sys.exit(130)
    except (OSError,ValueError,RuntimeError,TimeoutError) as error:
        print(str(error),file=sys.stderr);sys.exit(1)
