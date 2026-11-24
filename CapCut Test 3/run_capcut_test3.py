"""Local CapCut Test 3. Register saved daily reviews, then drive native export."""
import argparse
import ctypes
from ctypes import wintypes
from fractions import Fraction
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent))
import create_capcut_draft as draft

LOCAL=Path(os.environ['LOCALAPPDATA'])
ROOT=LOCAL/'CapCut/User Data/Projects/com.lveditor.draft'
REVIEW=LOCAL/'VlogSync'/'review-session.json'
BIN=os.environ.get('VLOGSYNC_FFMPEG_BIN')
PROBE=Path(BIN)/'ffprobe.exe' if BIN else Path(shutil.which('ffprobe.exe') or 'ffprobe.exe')
FFMPEG=Path(BIN)/'ffmpeg.exe' if BIN else Path(shutil.which('ffmpeg.exe') or 'ffmpeg.exe')
CAPCUT=LOCAL/'CapCut/Apps/CapCut.exe'
OUTPUT=Path.home()/'Videos/CapCut Test 3 Exports'
RUN=HERE/'Run 3'
PLAN=RUN/'plan.json'
ROUND={'Master':(1920,1080,'hevc'),'Proxy':(1280,720,'h264')}




# Native Windows shortcut adapted from frangonzz/capcut-auto-export
# commit 0f3cbf2ed579414ae323010fac27c978b58a79e8 (MIT).
# Copyright (c) 2026 capcut-auto-export contributors. See upstream-capcut-auto-export/LICENSE.
# This adapter scopes window actions to the expected project and never closes CapCut.
user=ctypes.WinDLL('user32',use_last_error=True)
user.GetForegroundWindow.restype=wintypes.HWND
user.PostMessageW.argtypes=(wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM)
user.SetForegroundWindow.argtypes=(wintypes.HWND,)
user.keybd_event.argtypes=(wintypes.BYTE,wintypes.BYTE,wintypes.DWORD,ctypes.c_size_t)


def windows():
    rows=[]
    callback=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    @callback
    def visit(handle,_):
        if user.IsWindowVisible(handle):
            title=ctypes.create_unicode_buffer(512)
            user.GetWindowTextW(handle,title,len(title))
            if title.value:
                pid=wintypes.DWORD()
                user.GetWindowThreadProcessId(handle,ctypes.byref(pid))
                rows.append((handle,pid.value,title.value))
        return True
    user.EnumWindows(visit,0)
    return rows


def activate(handle):
    user.SetForegroundWindow(handle)
    time.sleep(.25)
    if user.GetForegroundWindow()!=handle:
        raise RuntimeError('Windows did not give focus to the expected CapCut window. No key sent.')


def keyboard_key(vk):
    user.keybd_event(vk,0,0,0)
    try:time.sleep(.05)
    finally:user.keybd_event(vk,0,2,0)


def open_export(day, observed_window=None):
    item=project(day)
    locked=ROOT/item['title']/'.locked'
    if not locked.is_file():
        raise RuntimeError(f'Open the exact generated project first: {item["title"]}.')
    options=[(h,p,t) for h,p,t in windows() if t in ('CapCut',item['title'])]
    if observed_window is not None:
        options=[row for row in options if row[0]==observed_window]
    elif len(options)>1:
        options=[row for row in options if row[0]==user.GetForegroundWindow()]
    if len(options)!=1:
        raise RuntimeError(f'Expected one editor window; found {options}.')
    handle,pid,title=options[0]
    activate(handle)
    user.keybd_event(0x11,0,0,0)
    try:
        time.sleep(.08)
        if not user.PostMessageW(handle,0x0100,0x45,0x00120001):
            raise ctypes.WinError(ctypes.get_last_error())
        time.sleep(.10)
        user.PostMessageW(handle,0x0101,0x45,0xC0120001)
    finally:
        user.keybd_event(0x11,0,2,0)
    print('Upstream Ctrl+E trigger sent to CapCut:',day,pid,title)


def project(day):
    return next(p for p in draft.read(PLAN)['projects'] if p['date']==day)


def target(day,kind):
    item=project(day)
    stem=item.get('output_stem',day)
    if not isinstance(stem,str) or Path(stem).name!=stem or stem in ('','.','..'):
        raise RuntimeError('Unsafe output name in the run manifest')
    if item.get('vlog_number'):
        if stem!=f'Vlog {item["vlog_number"]}':
            raise RuntimeError('Vlog number does not match the reserved output filename')
        return OUTPUT/kind/f'{stem}.mp4'
    return OUTPUT/kind/f'{stem} - {kind}.mp4'


def trigger_receipt(day,kind):
    original=RUN/f'{day}-{kind}-trigger.json'
    attempts=[original,*RUN.glob(f'{day}-{kind}-trigger-retry-*.json')]
    return max((p for p in attempts if p.is_file()),key=lambda p:p.stat().st_mtime_ns)


def confirm_export(day,kind,retry=False):
    item=project(day)
    path=target(day,kind)
    if path.exists() or any(path.parent.glob(f'{path.stem}.*')):
        raise FileExistsError(path)
    if (RUN/f'{day}-{kind}.json').exists():
        raise RuntimeError('This output already has a verification receipt. Resolve its state before trying any export.')
    original=RUN/f'{day}-{kind}-trigger.json'
    if retry and not original.is_file():
        raise RuntimeError('A retry requires an earlier attempt record.')
    if not (ROOT/item['title']/'.locked').is_file():
        raise RuntimeError('The expected generated draft is no longer active.')
    candidates=[handle for handle,_,title in windows() if title==f'Export-{item["title"]}']
    if len(candidates)!=1:
        raise RuntimeError('CapCut does not show one exact matching export dialog.')
    activate(candidates[0])
    receipt=RUN/f'{day}-{kind}-trigger-retry-{time.time_ns()}.json' if retry else original
    draft.write_new(receipt,draft.encode(dict(path=str(path),timestamp=time.time())))
    keyboard_key(0x0D)
    print(f'Native export confirmation sent for {day} {kind}.')


def metadata(path,kind,expected_us):
    command=[str(PROBE),'-v','error','-show_entries',
             'stream=codec_type,codec_name,width,height,nb_frames,avg_frame_rate,start_time,duration,time_base,duration_ts,sample_rate,channels',
             '-of','json',str(path)]
    info=json.loads(subprocess.run(command,capture_output=True,check=True,timeout=15).stdout)
    video,=[s for s in info['streams'] if s['codec_type']=='video']
    audio=[s for s in info['streams'] if s['codec_type']=='audio']
    if (video['width'],video['height'],video['codec_name'])!=ROUND[kind]:
        raise RuntimeError(f'Unexpected output profile: {video}')
    if abs(float(video['duration'])-expected_us/1e6)>.125 or int(video['nb_frames'])<=0:
        raise RuntimeError(f'Output duration or frame count differs from the timeline: {video}')
    return dict(video=video,audio=audio,path=str(path),bytes=path.stat().st_size,
                last_write=path.stat().st_mtime)
