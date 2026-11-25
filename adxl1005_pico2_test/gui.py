#!/usr/bin/env python3
"""Graphical, standalone ADXL1005/Pico 2 recorder. No actuator controls."""
from datetime import datetime
from pathlib import Path
import queue
import csv
import subprocess
import shutil
import struct
import sys
import threading
import time
import zlib
import tkinter as tk
from tkinter import ttk,filedialog
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg,NavigationToolbar2Tk
import capture
import gui_support
import session

ROOT=Path(__file__).resolve().parent
BG='#f3f5f8'
INK='#172c43'
MUTED='#607185'
BLUE='#156e9b'


class PlotCanvas(FigureCanvasTkAgg):
    def draw(self):
        with capture.PLOT_LOCK:
            super().draw()


class TapperTest:
    def __init__(self,root):
        self.root=root
        root.title('Tapper test · ADXL1005 + Pico 2')
        root.geometry('1200x800')
        root.minsize(1040,700)
        root.configure(bg=BG)
        self.events=queue.Queue()
        self.choices=[]
        self.verified=None
        self.busy=None
        self.cancel_event=threading.Event()
        self.last_folder=None
        self.taps=[]
        self.session_started=None
        self.session_config=None
        self.closing=False
        self.advanced_window=None
        self.job_number=0
        self.port_label=tk.StringVar()
        self.show_all=tk.BooleanVar(value=False)
        self.or_wired=tk.BooleanVar(value=False)
        defaults={'mass':'73','threshold':'2','period':'30','window':'200','pre':'20','timeout':'30','cooldown':'2',
                  'supply':'3.3','reference':'3.3','sensitivity':''}
        self.values={name:tk.StringVar(value=value) for name,value in defaults.items()}
        style=ttk.Style(root)
        style.theme_use('clam')
        style.configure('.',font=('Helvetica Neue',12),background=BG,foreground=INK)
        style.configure('TButton',padding=(13,8),background='#e7edf3',borderwidth=0)
        style.map('TButton',background=[('active','#dbe5ef')],foreground=[('disabled','#94a0ad')])
        style.configure('Primary.TButton',background=BLUE,foreground='white',font=('Helvetica Neue',12,'bold'))
        style.map('Primary.TButton',background=[('disabled','#d3dbe3'),('active','#105779')],foreground=[('disabled','#8795a4')])
        style.configure('Cancel.TButton',foreground='#af2336')
        style.configure('TCombobox',padding=6)
        style.configure('TEntry',padding=6)
        self.build()
        self.refresh()
        root.after(50,self.drain_events)
        root.after(2000,self.auto_refresh)
        root.after(200,self.update_timer)
        root.protocol('WM_DELETE_WINDOW',self.close)

    def label(self,parent,text,**kwargs):
        options=dict(text=text,bg=parent.cget('bg'),fg=INK,font=('Helvetica Neue',12))
        options.update(kwargs)
        return tk.Label(parent,**options)

    def card(self,parent):
        return tk.Frame(parent,bg='white',highlightbackground='#dce3eb',highlightthickness=1,padx=16,pady=12)

    def build(self):
        shell=tk.Frame(self.root,bg=BG,padx=18,pady=14)
        shell.pack(fill='both',expand=True)
        header=tk.Frame(shell,bg=BG)
        header.pack(fill='x',pady=(0,12))
        title=tk.Frame(header,bg=BG)
        title.pack(side='left')
        self.label(title,'Tapper test',font=('Helvetica Neue',23,'bold')).pack(anchor='w')
        self.label(title,'ADXL1005 + Pico 2 · record here, fire taps separately in BrickMapper',fg=MUTED,font=('Helvetica Neue',11)).pack(anchor='w',pady=(2,0))
        self.connection_badge=self.label(header,'●  Not connected',fg=MUTED,font=('Helvetica Neue',11,'bold'))
        self.connection_badge.pack(side='right')
        body=tk.Frame(shell,bg=BG)
        body.pack(fill='both',expand=True)
        left=self.card(body)
        left.configure(width=310)
        left.pack(side='left',fill='y',padx=(0,12))
        left.pack_propagate(False)
        right=tk.Frame(body,bg=BG)
        right.pack(side='left',fill='both',expand=True)
        self.label(left,'PICO DEVICE',font=('Helvetica Neue',10,'bold'),fg=MUTED).pack(anchor='w',pady=(0,6))
        self.combo=ttk.Combobox(left,textvariable=self.port_label,state='readonly',width=32)
        self.combo.pack(fill='x')
        self.combo.bind('<<ComboboxSelected>>',self.selection_changed)
        connection_actions=tk.Frame(left,bg='white')
        connection_actions.pack(fill='x',pady=8)
        self.refresh_button=ttk.Button(connection_actions,text='Refresh',command=self.refresh)
        self.refresh_button.pack(side='left',padx=(0,8))
        self.connect_button=ttk.Button(connection_actions,text='Connect',style='Primary.TButton',command=self.connect)
        self.connect_button.pack(side='left',fill='x',expand=True)
        self.device_hint=self.label(left,'Select a Pico, then connect.',fg=MUTED,font=('Helvetica Neue',10),wraplength=274,justify='left')
        self.device_hint.pack(fill='x')
        self.show_all_button=ttk.Checkbutton(left,text='Show all serial devices',variable=self.show_all,command=self.refresh)
        self.show_all_button.pack(anchor='w',pady=(8,12))
        ttk.Separator(left).pack(fill='x',pady=(0,12))
        self.setting_widgets=[]
        fields=tk.Frame(left,bg='white')
        fields.pack(fill='x')
        for row,(name,text) in enumerate([('mass','Moving mass (g)'),('threshold','Trigger level (g)'),('period','Recording period (s)')]):
            self.label(fields,text,font=('Helvetica Neue',11)).grid(row=row,column=0,sticky='w',pady=7)
            entry=ttk.Entry(fields,textvariable=self.values[name],width=8)
            entry.grid(row=row,column=1,sticky='e',padx=(10,0),pady=7)
            self.setting_widgets.append(entry)
        fields.columnconfigure(0,weight=1)
        self.or_button=ttk.Checkbutton(left,text='ORout connected to GP15',variable=self.or_wired)
        self.or_button.pack(anchor='w',pady=(10,8))
        self.advanced_button=ttk.Button(left,text='Capture settings…',command=self.advanced)
        self.advanced_button.pack(fill='x',pady=(0,12))
        self.record_button=ttk.Button(left,text='Start recording',style='Primary.TButton',command=self.record)
        self.record_button.pack(fill='x',pady=(0,8))
        self.cancel_button=ttk.Button(left,text='Stop & save',style='Cancel.TButton',command=self.cancel)
        self.cancel_button.pack(fill='x')
        self.timer_label=self.label(left,'250,000 samples/s · 200 ms per tap',fg=MUTED,font=('Helvetica Neue',10),wraplength=274,justify='left')
        self.timer_label.pack(fill='x',pady=(10,6))
        self.label(left,'Only peaks ≥ 3 N count. Wait for ARMED; accepted taps have a settling pause (default 2 s). Gaps are not recorded.',fg=MUTED,font=('Helvetica Neue',10),wraplength=274,justify='left').pack(fill='x')
        self.demo_button=ttk.Button(header,text='Sample preview',command=self.demo)
        self.demo_button.pack(side='right',padx=(0,8))
        self.wiring_button=ttk.Button(header,text='Wiring & firmware',command=self.wiring)
        self.wiring_button.pack(side='right',padx=(0,10))
        self.results_button=ttk.Button(header,text='Open saved results',command=self.open_results)
        self.results_button.pack(side='right',padx=(0,8))

        self.status_box=tk.Frame(right,bg='#eaf0f6',padx=12,pady=10)
        self.status_box.pack(fill='x',pady=(0,10))
        self.status_text=self.label(self.status_box,'Select a Pico to get started.',font=('Helvetica Neue',12,'bold'),anchor='w',wraplength=760,justify='left')
        self.status_text.pack(fill='x')
        self.progress=ttk.Progressbar(self.status_box,mode='indeterminate')
        self.detail=self.label(self.status_box,'Start a session, switch to BrickMapper, and make your taps.',fg=MUTED,font=('Helvetica Neue',10),anchor='w',wraplength=760,justify='left')
        self.detail.pack(fill='x',pady=(3,0))
        metrics=tk.Frame(right,bg=BG)
        metrics.pack(fill='x',pady=(0,8))
        self.metric_labels=[]
        for index,text in enumerate(['PEAK ACCELERATION','INERTIAL-FORCE ESTIMATE','TAPS ≥ 3 N']):
            box=self.card(metrics)
            box.configure(padx=10,pady=7)
            box.grid(row=0,column=index,sticky='ew',padx=(0,6 if index<2 else 0))
            metrics.columnconfigure(index,weight=1,uniform='metrics')
            self.label(box,text,fg=MUTED,font=('Helvetica Neue',8,'bold')).pack(anchor='w')
            value=self.label(box,'—',font=('Helvetica Neue',17,'bold'))
            value.pack(anchor='w',pady=(2,0))
            self.metric_labels.append(value)
        exports=tk.Frame(right,bg=BG)
        exports.pack(fill='x',pady=(0,8))
        self.view_label=tk.StringVar(value='All taps')
        self.view_combo=ttk.Combobox(exports,textvariable=self.view_label,values=['All taps'],state='readonly',width=15)
        self.view_combo.pack(side='left')
        self.view_combo.bind('<<ComboboxSelected>>',lambda _event:self.redraw())
        self.csv_button=ttk.Button(exports,text='Export CSV…',command=self.export_csv)
        self.csv_button.pack(side='right',padx=(8,0))
        self.graph_button=ttk.Button(exports,text='Export graphs…',command=self.export_graphs)
        self.graph_button.pack(side='right')
        plot=tk.Frame(right,bg='white',highlightbackground='#dce3eb',highlightthickness=1)
        plot.pack(fill='both',expand=True)
        self.figure=Figure(figsize=(8,5),dpi=100,layout='constrained')
        self.canvas=PlotCanvas(self.figure,master=plot)
        self.toolbar=NavigationToolbar2Tk(self.canvas,plot,pack_toolbar=False)
        self.toolbar.update()
        self.toolbar.pack(side='bottom',fill='x')
        self.canvas.get_tk_widget().pack(side='top',fill='both',expand=True)
        axes=self.figure.subplots(2,1,sharex=True)
        for ax in axes:
            ax.grid(alpha=.12)
            ax.set_xlim(-20,180)
        axes[0].set_ylabel('Acceleration (g)')
        axes[0].text(.5,.5,'Your taps will appear here',ha='center',va='center',transform=axes[0].transAxes,color=MUTED)
        axes[1].set_ylabel('Inertial estimate (N)')
        axes[1].set_xlabel('Time from acceleration trigger (ms)')
        self.canvas.draw_idle()
        self.label(shell,'Force is an m × a estimate, not measured contact force. This app does not fire the solenoid.',fg=MUTED,font=('Helvetica Neue',10)).pack(side='bottom',before=body,anchor='w',pady=(8,0))

    def selected(self):
        return next((item for item in self.choices if item.label==self.port_label.get()),None)

    def refresh(self):
        old=self.selected()
        try: choices=gui_support.devices(self.show_all.get())
        except Exception as error:
            self.set_status(f'Could not list USB devices: {error}',True)
            return
        self.choices=choices
        self.combo.configure(values=[item.label for item in choices])
        match=next((item for item in choices if old and item.key==old.key),None)
        if match: self.port_label.set(match.label)
        elif len(choices)==1: self.port_label.set(choices[0].label)
        else: self.port_label.set('')
        if self.verified and not any(item.key==self.verified.key for item in choices):
            self.verified=None
            self.cancel_event.set()
            self.set_status('Pico disconnected. Reconnect it and click Connect.',True)
        self.device_hint.configure(text='No Pico serial device found. Load the included test firmware, then reconnect USB.' if not choices
                                   else 'Select your Pico, then click Connect. Only the ADXL test recorder will be accepted.')
        self.buttons()

    def auto_refresh(self):
        if not self.closing:
            self.refresh()
            self.root.after(2000,self.auto_refresh)

    def update_timer(self):
        if self.busy=='session' and self.session_started is not None:
            left=max(0,self.session_config['period_s']-(time.monotonic()-self.session_started))
            self.timer_label.configure(text=f'{left:.0f} s left · {len(self.taps)} taps captured' if left else 'Finishing the current tap…')
        if not self.closing:self.root.after(200,self.update_timer)

    def selection_changed(self,_event=None):
        self.verified=None
        self.set_status('Click Connect to verify the selected Pico.')
        self.buttons()

    def buttons(self):
        busy=bool(self.busy)
        self.combo.configure(state='disabled' if busy else 'readonly')
        for widget in [self.refresh_button,self.show_all_button,self.demo_button,self.advanced_button,*self.setting_widgets,self.or_button]:
            widget.configure(state='disabled' if busy else 'normal')
        self.connect_button.configure(state='disabled' if busy or not self.selected() else 'normal',
                                      text='Disconnect' if self.verified else 'Connect')
        self.record_button.configure(state='normal' if self.verified and not busy else 'disabled')
        self.cancel_button.configure(state='normal' if self.busy in ('connect','session') else 'disabled')
        self.results_button.configure(state='normal' if self.last_folder else 'disabled')
        self.csv_button.configure(state='normal' if self.last_folder and self.taps and not busy else 'disabled')
        self.graph_button.configure(state='normal' if self.taps and not busy else 'disabled')
        self.connection_badge.configure(text='●  Pico ready' if self.verified else '●  Not connected',
                                        fg='#20734f' if self.verified else MUTED)

    def set_status(self,message,error=False):
        self.status_text.configure(text=message,fg='#af2336' if error else INK)

    def config(self):
        return gui_support.settings({name:value.get() for name,value in self.values.items()})

    def run_job(self,kind,work):
        if self.busy: return
        if self.advanced_window and self.advanced_window.winfo_exists(): self.advanced_window.destroy()
        self.busy=kind
        self.cancel_event=threading.Event()
        self.job_number+=1
        job=self.job_number
        cancel=self.cancel_event
        self.progress.pack(fill='x',pady=(6,0))
        self.progress.start(12)
        self.buttons()
        def worker():
            try:
                result=work(cancel,lambda phase,text:self.events.put((job,'progress',(phase,text))))
                if kind!='session':capture.check_cancel(cancel)
                self.events.put((job,'done',result))
            except Exception as error:
                self.events.put((job,'error',error))
        threading.Thread(target=worker,daemon=True).start()

    def connect(self):
        if self.verified:
            self.verified=None
            self.set_status('Pico disconnected from this test window.')
            self.buttons()
            return
        device=self.selected()
        if not device: return
        self.set_status('Checking the selected Pico…')
        def work(cancel,_progress):
            capture.probe(device.port,cancel)
            return ('connected',device)
        self.run_job('connect',work)

    def record(self):
        if not self.verified or self.busy: return
        device=self.verified
        if not any(item.key==device.key for item in self.choices):
            self.verified=None;self.refresh();return
        try: config=self.config()
        except ValueError as error: self.set_status(str(error),True);return
        or_wired=self.or_wired.get()
        self.session_config=config
        self.session_started=time.monotonic()
        self.taps=[];self.last_folder=None
        self.view_label.set('All taps')
        for label in self.metric_labels:label.configure(text='—')
        with capture.PLOT_LOCK:
            self.figure.clear();self.figure.suptitle('Waiting for taps during the recording period…')
        self.canvas.draw_idle()
        self.set_status('Starting the recording period. Hold the tip still for calibration…')
        def work(cancel,progress):
            result=session.record_session(device.port,config,config['period_s'],or_wired,cancel,progress,
                lambda tap:self.events.put((self.job_number,'tap',tap)))
            progress('saving','Saving the session…')
            folder=ROOT/'captures'/('session-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
            folder,previews=session.save_session(result,folder,config,progress)
            return ('session',(result,folder,previews))
        self.run_job('session',work)

    def demo(self):
        try: config=self.config()
        except ValueError as error: self.set_status(str(error),True);return
        or_wired=self.or_wired.get()
        self.set_status('Building sample plots. No Pico will be accessed.')
        def work(cancel,_progress):
            taps=[]
            for number,gain in enumerate([1,.8,1.1],start=1):
                meta,raw=capture.demo(config['count'],config['pre'],or_wired)
                words=[max(0,min(4095,round(2048+(word[0]-2048)*gain))) for word in struct.iter_unpack('<H',raw)]
                raw=struct.pack('<'+'H'*len(words),*words)
                meta['crc32']=zlib.crc32(raw)
                taps.append(dict(number=number,meta=meta,raw=raw,received_offset_s=number))
            capture.check_cancel(cancel)
            result=dict(taps=taps,requested_period_s=3,elapsed_s=3,stopped=False,issue=None)
            folder=ROOT/'captures'/('SAMPLE-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
            folder,previews=session.save_session(result,folder,config)
            self.session_config=config
            return ('session',(result,folder,previews))
        self.run_job('demo',work)

    def cancel(self):
        if self.busy:
            self.cancel_event.set()
            self.set_status('Stopping and saving completed taps…' if self.busy=='session' else 'Cancelling…')
            self.cancel_button.configure(state='disabled')

    def drain_events(self):
        try:
            while True:
                job,kind,payload=self.events.get_nowait()
                if job!=self.job_number: continue
                if kind=='progress':
                    phase,text=payload
                    if not self.cancel_event.is_set() or phase=='saving': self.set_status(text)
                    if phase=='saving': self.cancel_button.configure(state='disabled')
                    continue
                if kind=='tap':
                    item=dict(payload)
                    config=self.session_config
                    rows,summary=capture.convert(item['meta'],item['raw'],config['mass_g'],config['supply_v'],
                        config['adc_reference_v'],config['sensitivity_mv_g'])
                    item.update(summary=summary,preview_rows=session.preview_rows(rows))
                    self.taps.append(item)
                    self.view_combo.configure(values=['All taps',*[f"Tap {item['number']}" for item in self.taps]])
                    self.redraw()
                    continue
                self.busy=None
                self.progress.stop();self.progress.pack_forget()
                if kind=='error':
                    self.verified=None
                    self.set_status(str(payload),not isinstance(payload,capture.CaptureCancelled))
                elif payload[0]=='connected':
                    device=payload[1]
                    if any(item.key==device.key for item in self.choices):
                        self.verified=device
                        self.set_status('Pico verified. Set a recording period, then click Start recording.')
                elif payload[0]=='session':
                    result,self.last_folder,previews=payload[1]
                    old={item['number']:item for item in self.taps}
                    for item in previews:
                        if item['number'] in old and 'raw' in old[item['number']]:item['raw']=old[item['number']]['raw']
                    self.taps=previews
                    self.view_label.set('All taps')
                    self.view_combo.configure(values=['All taps',*[f"Tap {item['number']}" for item in self.taps]])
                    self.redraw()
                    if not self.taps:
                        with capture.PLOT_LOCK:
                            self.figure.clear();self.figure.suptitle('No taps captured in this session')
                        self.metric_labels[2].configure(text='0')
                        self.canvas.draw_idle()
                    simulated=any(item['meta'].get('simulated') for item in self.taps)
                    self.timer_label.configure(text=f"{len(self.taps)} taps saved · {result['elapsed_s']:.1f} s recording period")
                    if simulated:self.set_status('SIMULATED SAMPLE — no sensor readings. Try the CSV and graph export buttons.')
                    elif result['issue']:
                        self.verified=None
                        self.set_status(f"Saved {len(self.taps)} complete taps. {result['issue']}",True)
                    else:self.set_status(f"Session {'stopped' if result['stopped'] else 'finished'}: {len(self.taps)} taps saved.")
                    warnings=list(dict.fromkeys(value for item in self.taps for value in item['summary']['software_warnings']))
                    ignored=result.get('ignored_below_minimum_count',0)
                    note=f'{ignored} captures below 3 N ignored. '
                    self.detail.configure(text=note+(' · '.join(warnings) if warnings else 'Export CSV for full-rate samples; export graphs for the current view. Quiet gaps between taps are not recorded.'),
                                          fg='#91601c' if warnings else MUTED)
                self.buttons()
        except queue.Empty:
            pass
        if self.closing and not self.busy:
            self.root.destroy();return
        self.root.after(50,self.drain_events)

    def open_folder(self,folder):
        if sys.platform=='darwin': subprocess.Popen(['open',str(folder)])
        elif sys.platform=='win32':
            import os
            os.startfile(folder)
        else: subprocess.Popen(['xdg-open',str(folder)])

    def open_results(self):
        if self.last_folder: self.open_folder(self.last_folder)

    def selected_taps(self):
        if self.view_label.get()=='All taps':return self.taps
        return [item for item in self.taps if self.view_label.get()==f"Tap {item['number']}"]

    def redraw(self):
        selected=self.selected_taps()
        if not selected:return
        if len(selected)==1:
            item=selected[0]
            raw=item.get('raw')
            if raw is None:raw=(item['folder']/'raw_adc_u16le.bin').read_bytes()
            summary=item['summary']
            rows,_=capture.convert(item['meta'],raw,summary['moving_mass_g'] or 0,summary['sensor_supply_v'],
                                  summary['adc_reference_v'],summary['sensitivity_mv_per_g'])
            capture.draw_plot(self.figure,rows,item['meta'],summary['moving_mass_g'] or 0)
        else:session.draw_session(self.figure,selected)
        self.toolbar.update();self.canvas.draw_idle()
        peak=max(item['summary']['observed_peak_abs_acceleration_g'] for item in selected)
        forces=[item['summary']['observed_peak_abs_inertial_force_estimate_N'] for item in selected]
        self.metric_labels[0].configure(text=f'{peak:.2f} g')
        self.metric_labels[1].configure(text=f'{max(value for value in forces if value is not None):.2f} N' if any(value is not None for value in forces) else 'Not calculated')
        self.metric_labels[2].configure(text=str(len(self.taps)))

    def export_csv(self):
        if not self.last_folder or not self.taps:return
        selected=self.selected_taps()
        if not selected:return
        path=filedialog.asksaveasfilename(parent=self.root,title='Export full-rate CSV',defaultextension='.csv',
            initialfile='all_taps.csv' if len(selected)==len(self.taps) else 'selected_tap.csv',filetypes=[('CSV data','*.csv')])
        if not path:return
        try:
            if len(selected)==len(self.taps):
                source=self.last_folder/'all_taps.csv'
                if Path(path).resolve()!=source.resolve():shutil.copyfile(source,path)
            else:
                item=selected[0];summary=item['summary']
                raw=item.get('raw') or (item['folder']/'raw_adc_u16le.bin').read_bytes()
                rows,_=capture.convert(item['meta'],raw,summary['moving_mass_g'] or 0,summary['sensor_supply_v'],
                                      summary['adc_reference_v'],summary['sensitivity_mv_per_g'])
                with Path(path).open('w',newline='') as stream:
                    writer=csv.DictWriter(stream,fieldnames=['tap_number',*rows[0]])
                    writer.writeheader();writer.writerows(dict(tap_number=item['number'],**row) for row in rows)
            self.set_status(f'CSV exported: {Path(path).name}')
        except (OSError,ValueError) as error:self.set_status(f'Could not export CSV: {error}',True)

    def export_graphs(self):
        if not self.taps:return
        path=filedialog.asksaveasfilename(parent=self.root,title='Export graph image',defaultextension='.png',
            initialfile='tap_graphs.png',filetypes=[('PNG image','*.png'),('PDF document','*.pdf'),('SVG graphic','*.svg')])
        if not path:return
        try:
            with capture.PLOT_LOCK:self.figure.savefig(path,dpi=200)
            self.set_status(f'Graphs exported: {Path(path).name}')
        except (OSError,ValueError) as error:self.set_status(f'Could not export graphs: {error}',True)

    def wiring(self):
        window=tk.Toplevel(self.root)
        window.title('Wiring & Pico firmware')
        window.configure(bg=BG,padx=22,pady=20)
        window.transient(self.root)
        self.label(window,'Sensor board → Pico 2',font=('Helvetica Neue',18,'bold')).pack(anchor='w',pady=(0,12))
        lines='VDD  →  3V3 OUT, physical pin 36\nGND  →  AGND, physical pin 33\nVout  →  GP26 / ADC0, physical pin 31\nST + STBY  →  GND\nORout  →  GP15, physical pin 20 (optional)'
        self.label(window,lines,justify='left',font=('Helvetica Neue',13),anchor='w').pack(anchor='w')
        self.label(window,'Use 3.3 V, not 5 V. Align the sensitive axis with tip travel.\nNo solenoid coil wire connects to the Pico.',justify='left',fg=MUTED).pack(anchor='w',pady=14)
        self.label(window,'First-time setup: hold BOOTSEL while connecting USB, then copy\nadxl1005_test.uf2 from the firmware folder to the Pico boot drive.',justify='left').pack(anchor='w')
        ttk.Button(window,text='Open firmware folder',command=lambda:self.open_folder(ROOT/'firmware/prebuilt')).pack(anchor='w',pady=(14,0))

    def advanced(self):
        if self.advanced_window and self.advanced_window.winfo_exists():
            self.advanced_window.lift();return
        window=tk.Toplevel(self.root)
        self.advanced_window=window
        window.title('Capture settings')
        window.configure(bg=BG,padx=22,pady=18)
        window.transient(self.root)
        for row,(name,label) in enumerate([('window','Per-tap window (ms)'),('pre','Before trigger (ms)'),('cooldown','Pause after capture (s)'),
                ('supply','Sensor supply (V)'),('reference','ADC reference (V)'),('sensitivity','Sensitivity (mV/g; blank = nominal)')]):
            self.label(window,label).grid(row=row,column=0,sticky='w',padx=(0,15),pady=5)
            ttk.Entry(window,textvariable=self.values[name],width=12).grid(row=row,column=1,pady=5)
        def apply():
            try: self.config()
            except ValueError as error:
                feedback.configure(text=str(error));return
            window.destroy()
        feedback=self.label(window,'250,000 samples/s · 4–240 ms recording window',fg=MUTED)
        feedback.grid(row=7,column=0,columnspan=2,pady=12)
        ttk.Button(window,text='Done',style='Primary.TButton',command=apply).grid(row=8,column=1,sticky='e')

    def close(self):
        self.closing=True
        self.cancel_event.set()
        if not self.busy: self.root.destroy()
        else:
            self.set_status('Saving completed taps before closing…')


def main():
    root=tk.Tk()
    TapperTest(root)
    root.mainloop()


if __name__=='__main__':main()
