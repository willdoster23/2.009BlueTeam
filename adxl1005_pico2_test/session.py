"""Timed sessions of separate, high-rate triggered taps; gaps are not recorded."""
import csv
from datetime import datetime,timezone
import json
import math
from pathlib import Path
import time
import capture


MIN_TAP_FORCE_N=3.0


def counts_as_tap(summary):
    peak=summary.get('observed_peak_abs_inertial_force_estimate_N')
    return peak is not None and math.isfinite(peak) and peak>=MIN_TAP_FORCE_N


def record_session(port,config,period_s,or_wired,cancel_event,progress,on_tap):
    if not 1<=period_s<=120: raise ValueError('Recording period must be between 1 and 120 seconds.')
    cooldown=float(config.get('cooldown_s',2))
    if not math.isfinite(cooldown) or not 0<=cooldown<=10:
        raise ValueError('Pause after each capture must be between 0 and 10 seconds.')
    if not math.isfinite(config['mass_g']) or config['mass_g']<=0:
        raise ValueError('Enter a positive moving mass to apply the 3 N tap minimum.')
    started=time.monotonic()
    deadline=started+period_s
    taps=[]
    ignored=0
    issue=None
    while not cancel_event.is_set():
        remaining=deadline-time.monotonic()
        if remaining<.1: break
        number=len(taps)+1
        def report(phase,text):
            if phase=='armed': text=f'ARMED for tap {number} — fire the tapper in BrickMapper.'
            progress(phase,text)
        try:
            meta,raw=capture.record(port,config['threshold_counts'],config['count'],config['pre'],
                                    min(remaining,60),report,cancel_event)
            received_offset_s=time.monotonic()-started
            meta['or_wired']=or_wired
            _rows,summary=capture.convert(meta,raw,config['mass_g'],config['supply_v'],
                                         config['adc_reference_v'],config['sensitivity_mv_g'])
        except capture.CaptureCancelled:
            break
        except Exception as error:
            if 'AXL1 ERROR trigger_timeout' in str(error): continue
            issue=str(error)
            break
        if not counts_as_tap(summary):
            ignored+=1
            peak=summary['observed_peak_abs_inertial_force_estimate_N']
            progress('ignored',f'Ignored motion: {peak:.2f} N peak is below {MIN_TAP_FORCE_N:g} N. Re-arming…')
            continue
        tap=dict(number=number,meta=meta,raw=raw,received_offset_s=received_offset_s)
        taps.append(tap)
        on_tap(tap)
        pause=min(cooldown,max(0,deadline-time.monotonic()))
        if pause>0 and not cancel_event.is_set():
            progress('cooldown',f'Capture {number} saved. Let the tip settle; re-arming after a {pause:.1f} s pause. Wait for ARMED.')
            if cancel_event.wait(pause): break
    return dict(taps=taps,requested_period_s=period_s,elapsed_s=time.monotonic()-started,
                stopped=cancel_event.is_set(),issue=issue,cooldown_s=cooldown,
                minimum_tap_force_N=MIN_TAP_FORCE_N,ignored_below_minimum_count=ignored)


def preview_rows(rows,limit=2500):
    """Display min/max envelopes, preserving extrema; full-rate samples stay in CSV."""
    if len(rows)<=limit: return rows
    block=max(1,(len(rows)+limit//2-1)//(limit//2))
    result=[]
    for start in range(0,len(rows),block):
        group=rows[start:start+block]
        valid=[row for row in group if row['acceleration_g'] is not None]
        chosen=[]
        if valid:
            chosen=[min(valid,key=lambda row:row['acceleration_g']),max(valid,key=lambda row:row['acceleration_g'])]
        bad=next((row for row in group if row['acceleration_g'] is None),None)
        if bad is not None: chosen.append(bad)
        for row in sorted({row['time_s']:row for row in chosen}.values(),key=lambda row:row['time_s']):
            result.append(row)
    return result


def draw_session(figure,taps):
    if len(taps)==1:
        item=taps[0]
        capture.draw_plot(figure,item['preview_rows'],item['meta'],item['summary']['moving_mass_g'] or 0)
        return
    with capture.PLOT_LOCK:
        figure.clear()
        axes=figure.subplots(2,1,sharex=True)
        simulated=any(item['meta'].get('simulated') for item in taps)
        for item in taps:
            rows=item['preview_rows']
            x=[row['time_s']*1000 for row in rows]
            line,=axes[0].plot(x,[row['acceleration_g'] for row in rows],lw=.8,alpha=.8,label=f"Tap {item['number']}")
            axes[1].plot(x,[row['inertial_force_estimate_N'] for row in rows],lw=.8,alpha=.8,color=line.get_color())
        for ax in axes:
            ax.axvline(0,color='#777777',lw=.7,ls='--')
            ax.grid(alpha=.15)
        if len(taps)<=10: axes[0].legend(loc='upper right',fontsize=8,ncol=min(5,len(taps)))
        axes[0].set_ylabel('Axial acceleration (g)')
        axes[1].set_ylabel('Inertial-force estimate (N)')
        axes[1].set_xlabel('Time from each tap’s acceleration trigger (ms)')
        figure.suptitle(('SIMULATED SAMPLE — ' if simulated else '')+f'{len(taps)} taps aligned for comparison')
        figure.supxlabel('Display envelopes; CSV keeps every sample. Quiet gaps are not recorded.\nForce is m×a, not measured contact force.',fontsize=8)


def save_session(result,folder,config,progress=lambda *_:None):
    folder=Path(folder)
    if folder.exists() and any(folder.iterdir()):raise ValueError('Choose a fresh output folder.')
    folder.mkdir(parents=True,exist_ok=True)
    previews=[]
    ignored=result.get('ignored_below_minimum_count',0)
    with (folder/'all_taps.csv').open('w',newline='') as stream:
        writer=None
        for item in result['taps']:
            progress('saving',f"Saving tap {item['number']} of {len(result['taps'])}…")
            rows,summary=capture.convert(item['meta'],item['raw'],config['mass_g'],config['supply_v'],
                                         config['adc_reference_v'],config['sensitivity_mv_g'])
            if not counts_as_tap(summary):
                ignored+=1
                continue
            number=len(previews)+1
            if writer is None:
                writer=csv.DictWriter(stream,fieldnames=['tap_number',*rows[0].keys()])
                writer.writeheader()
            writer.writerows(dict(tap_number=number,**row) for row in rows)
            tap_folder=folder/f'tap-{number:03d}'
            tap_folder.mkdir()
            (tap_folder/'raw_adc_u16le.bin').write_bytes(item['raw'])
            (tap_folder/'capture.json').write_text(json.dumps(dict(capture=item['meta'],analysis=summary,
                received_offset_s=item['received_offset_s'],receivedTimeNote='Host receive time, not a precise impact timestamp.'),indent=2)+'\n')
            previews.append(dict(number=number,meta=item['meta'],summary=summary,
                                 preview_rows=preview_rows(rows),folder=tap_folder))
    info={key:value for key,value in result.items() if key!='taps'}
    info.update(savedAt=datetime.now(timezone.utc).isoformat(),tap_count=len(previews),
                recording_mode='triggered_tap_windows',quiet_time_recorded=False,
                cooldown_s=config.get('cooldown_s',2),
                minimum_tap_force_N=MIN_TAP_FORCE_N,ignored_below_minimum_count=ignored,
                count_note='Tap count includes only windows with peak absolute inertial-force estimate >= 3 N; not a verified physical tap count.',
                gap_note='There are re-arm/transfer gaps. Wait for ARMED before each tap.',
                force_is_contact_force=False,moving_mass_g=config['mass_g'])
    (folder/'session.json').write_text(json.dumps(info,indent=2)+'\n')
    if previews:
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        with capture.PLOT_LOCK:
            figure=Figure(figsize=(11,7),layout='constrained')
            FigureCanvasAgg(figure)
            draw_session(figure,previews)
            figure.savefig(folder/'session_graphs.png',dpi=180)
            figure.clear()
    return folder,previews
