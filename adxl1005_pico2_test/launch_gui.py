#!/usr/bin/env python3
"""Launch directly, or show a first-time setup window for local dependencies."""
import importlib.util
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk

ROOT=Path(__file__).resolve().parent


def ready(python=None):
    if python:
        return subprocess.run([str(python),'-c','import tkinter,serial,matplotlib'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
    return all(importlib.util.find_spec(name) is not None for name in ('tkinter','serial','matplotlib'))


def run(python):
    os.execv(str(python),[str(python),str(ROOT/'gui.py')])


def main():
    local_python=ROOT/'.venv/bin/python'
    selected=sys.executable if ready() else (local_python if local_python.exists() and ready(local_python) else None)
    font_cache=Path(os.environ.get('MPLCONFIGDIR',ROOT/'.cache/matplotlib'))
    if selected and any(font_cache.glob('fontlist*.json')): run(selected)
    root=tk.Tk()
    root.title('Preparing Tapper test')
    root.geometry('510x180')
    root.configure(bg='#f3f5f8')
    ttk.Label(root,text='Preparing your test window',font=('Helvetica Neue',18,'bold')).pack(anchor='w',padx=22,pady=(20,9))
    text=tk.StringVar(value='Loading the test window. The first launch may take a minute.')
    ttk.Label(root,textvariable=text,wraplength=460).pack(anchor='w',padx=22)
    progress=ttk.Progressbar(root,mode='indeterminate')
    progress.pack(fill='x',padx=22,pady=16);progress.start()
    events=queue.Queue()
    def setup():
        try:
            cache=ROOT/'.cache';cache.mkdir(exist_ok=True)
            with (cache/'setup.log').open('w') as log:
                target=selected
                if target is None:
                    if not local_python.exists():subprocess.run([sys.executable,'-m','venv',str(ROOT/'.venv')],check=True,stdout=log,stderr=subprocess.STDOUT)
                    subprocess.run([str(local_python),'-m','pip','install','-r',str(ROOT/'requirements.txt')],check=True,stdout=log,stderr=subprocess.STDOUT)
                    target=local_python
                subprocess.run([str(target),'-c','from matplotlib.figure import Figure'],check=True,stdout=log,stderr=subprocess.STDOUT)
            if not ready(target): raise RuntimeError('Python dependencies did not load.')
            events.put((target,None))
        except Exception as error: events.put((None,error))
    def check():
        try: target,result=events.get_nowait()
        except queue.Empty:
            root.after(100,check);return
        progress.stop()
        if result is None:
            root.destroy();run(target)
        else:
            text.set('Setup could not finish. Check your internet connection and reopen the app. Details are in .cache/setup.log.')
    threading.Thread(target=setup,daemon=True).start()
    root.after(100,check)
    root.mainloop()


if __name__=='__main__':main()
