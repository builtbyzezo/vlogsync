"""Local, foreground-checked Windows controls for the installed English CapCut UI."""
from __future__ import annotations

import base64
import ctypes as ct
from ctypes import wintypes as wt
from dataclasses import dataclass
import io
import json
from pathlib import Path
import queue
import re
import subprocess
import threading
import time

from PIL import Image


user = ct.WinDLL('user32', use_last_error=True)
kernel = ct.WinDLL('kernel32', use_last_error=True)
gdi = ct.WinDLL('gdi32', use_last_error=True)
user.SetProcessDpiAwarenessContext(ct.c_void_p(-4))
user.GetForegroundWindow.restype = wt.HWND
user.GetWindowRect.argtypes = wt.HWND, ct.POINTER(wt.RECT)
user.GetWindowTextW.argtypes = wt.HWND, wt.LPWSTR, ct.c_int
user.GetWindowThreadProcessId.argtypes = wt.HWND, ct.POINTER(wt.DWORD)
user.SetForegroundWindow.argtypes = (wt.HWND,)
user.IsIconic.argtypes = (wt.HWND,)
user.IsIconic.restype = wt.BOOL
user.ShowWindowAsync.argtypes = (wt.HWND, wt.INT)
user.ShowWindowAsync.restype = wt.BOOL
user.AttachThreadInput.argtypes = wt.DWORD, wt.DWORD, wt.BOOL
user.PeekMessageW.argtypes = ct.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT
user.GetAncestor.argtypes = wt.HWND, wt.UINT
user.GetAncestor.restype = wt.HWND
user.GetWindow.argtypes = wt.HWND, wt.UINT
user.GetWindow.restype = wt.HWND
user.WindowFromPoint.argtypes = (wt.POINT,)
user.WindowFromPoint.restype = wt.HWND
user.PostMessageW.argtypes = wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM
user.keybd_event.argtypes = wt.BYTE, wt.BYTE, wt.DWORD, ct.c_size_t
user.mouse_event.argtypes = wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, ct.c_size_t
kernel.OpenProcess.argtypes = wt.DWORD, wt.BOOL, wt.DWORD
kernel.OpenProcess.restype = wt.HANDLE
kernel.CloseHandle.argtypes = (wt.HANDLE,)
kernel.GetCurrentThreadId.restype = wt.DWORD
kernel.QueryFullProcessImageNameW.argtypes = wt.HANDLE, wt.DWORD, wt.LPWSTR, ct.POINTER(wt.DWORD)
uptr = ct.c_size_t


class Keyboard(ct.Structure):
    _fields_ = [('vk', wt.WORD), ('scan', wt.WORD), ('flags', wt.DWORD),
                ('time', wt.DWORD), ('extra', uptr)]


class Mouse(ct.Structure):
    _fields_ = [('dx', wt.LONG), ('dy', wt.LONG), ('data', wt.DWORD),
                ('flags', wt.DWORD), ('time', wt.DWORD), ('extra', uptr)]


class InputData(ct.Union):
    _fields_ = [('key', Keyboard), ('mouse', Mouse)]


class Input(ct.Structure):
    _fields_ = [('type', wt.DWORD), ('data', InputData)]


user.SendInput.argtypes = wt.UINT, ct.POINTER(Input), ct.c_int
user.SendMessageTimeoutW.argtypes = (wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM, wt.UINT,
                                    wt.UINT, ct.POINTER(uptr))
user.SendMessageTimeoutW.restype = wt.LPARAM
user.GetDC.argtypes = (wt.HWND,)
user.GetDC.restype = wt.HDC
user.ReleaseDC.argtypes = wt.HWND, wt.HDC
user.PrintWindow.argtypes = wt.HWND, wt.HDC, wt.UINT
gdi.CreateCompatibleDC.argtypes = (wt.HDC,)
gdi.CreateCompatibleDC.restype = wt.HDC
gdi.CreateCompatibleBitmap.argtypes = wt.HDC, ct.c_int, ct.c_int
gdi.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi.SelectObject.argtypes = wt.HDC, wt.HGDIOBJ
gdi.SelectObject.restype = wt.HGDIOBJ
gdi.GetDIBits.argtypes = wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ct.c_void_p, ct.c_void_p, wt.UINT
gdi.DeleteObject.argtypes = (wt.HGDIOBJ,)
gdi.DeleteDC.argtypes = (wt.HDC,)


class BitmapHeader(ct.Structure):
    _fields_ = [('size', wt.DWORD), ('width', wt.LONG), ('height', wt.LONG),
                ('planes', wt.WORD), ('bits', wt.WORD), ('compression', wt.DWORD),
                ('length', wt.DWORD), ('xppm', wt.LONG), ('yppm', wt.LONG),
                ('colors_used', wt.DWORD), ('colors_important', wt.DWORD)]


class GuiThread(ct.Structure):
    _fields_ = [('size', wt.DWORD), ('flags', wt.DWORD), ('active', wt.HWND),
                ('focus', wt.HWND), ('capture', wt.HWND), ('menu', wt.HWND),
                ('move', wt.HWND), ('caret', wt.HWND), ('area', wt.RECT)]


def focused_edit(window):
    result = GuiThread()
    result.size = ct.sizeof(result)
    thread = user.GetWindowThreadProcessId(window, None)
    if not user.GetGUIThreadInfo(thread, ct.byref(result)) or not result.focus:
        raise RuntimeError('Unable to read the Windows folder picker edit control')
    class_name = ct.create_unicode_buffer(100)
    user.GetClassNameW(result.focus, class_name, len(class_name))
    if class_name.value != 'Edit' or user.GetAncestor(result.focus, 2) != window:
        raise RuntimeError('The exact Windows folder field is not focused')
    value = ct.create_unicode_buffer(2048)
    done = uptr()
    if not user.SendMessageTimeoutW(result.focus, 0x000D, len(value), ct.addressof(value),
                                    0x0002, 1000, ct.byref(done)):
        raise RuntimeError('Windows did not return the exact folder path')
    return value.value


def capture_window(window, bounds):
    """Ask the specific window to render into a bitmap instead of scraping other applications."""
    desktop = user.GetDC(None)
    dc = gdi.CreateCompatibleDC(desktop)
    bmp = gdi.CreateCompatibleBitmap(desktop, bounds.w, bounds.h)
    prev = gdi.SelectObject(dc, bmp)
    try:
        if not user.PrintWindow(window, dc, 2):
            raise RuntimeError('Windows could not capture the specific CapCut window')
        gdi.SelectObject(dc, prev)
        header = BitmapHeader(ct.sizeof(BitmapHeader), bounds.w, -bounds.h, 1, 32, 0)
        data = ct.create_string_buffer(bounds.w * bounds.h * 4)
        if gdi.GetDIBits(dc, bmp, 0, bounds.h, data, ct.byref(header), 0) != bounds.h:
            raise RuntimeError('Windows returned an incomplete CapCut image')
        return Image.frombytes('RGB', (bounds.w,bounds.h), data.raw, 'raw', 'BGRX')
    finally:
        gdi.SelectObject(dc, prev)
        gdi.DeleteObject(bmp)
        gdi.DeleteDC(dc)
        user.ReleaseDC(None, desktop)


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    w: int
    h: int

    @property
    def center(self):
        return self.x + self.w // 2, self.y + self.h // 2


def canonical(s):
    return re.sub(r'[^a-z0-9]+', '', s.casefold())


def title(handle):
    text = ct.create_unicode_buffer(512)
    user.GetWindowTextW(handle, text, len(text))
    return text.value


def pid(handle):
    value = wt.DWORD()
    user.GetWindowThreadProcessId(handle, ct.byref(value))
    return value.value


def process_image(process):
    h = kernel.OpenProcess(0x1000, False, process)
    if not h:
        return None
    try:
        buf = ct.create_unicode_buffer(2048)
        count = wt.DWORD(len(buf))
        if kernel.QueryFullProcessImageNameW(h, 0, buf, ct.byref(count)):
            return Path(buf.value)
    finally:
        kernel.CloseHandle(h)
    return None


def rect(handle):
    out = wt.RECT()
    if not user.GetWindowRect(handle, ct.byref(out)):
        raise RuntimeError('A CapCut window disappeared')
    return Box(out.left, out.top, out.right - out.left, out.bottom - out.top)


def keys(*codes):
    for vk in codes:
        user.keybd_event(vk, 0, 0, 0)
    for vk in reversed(codes):
        user.keybd_event(vk, 0, 2, 0)


def foreground(handle):
    if user.GetForegroundWindow() == handle:
        return True
    user.SetForegroundWindow(handle)
    time.sleep(.25)
    if user.GetForegroundWindow() == handle:
        return True
    # Attach only for the foreground transition; no global settings are changed.
    old = user.GetForegroundWindow()
    thread = user.GetWindowThreadProcessId(old, None)
    current = kernel.GetCurrentThreadId()
    user.PeekMessageW(ct.byref(wt.MSG()), None, 0, 0, 0)
    if thread and current != thread and user.AttachThreadInput(current, thread, True):
        try:
            user.SetForegroundWindow(handle)
        finally:
            user.AttachThreadInput(current, thread, False)
        time.sleep(.25)
    if user.GetForegroundWindow() != handle:
        # Preserve the upstream fallback for a foreground lock after application launch.
        keys(0x12)
        user.SetForegroundWindow(handle)
        time.sleep(.35)
    return user.GetForegroundWindow() == handle


def send_text(text):
    raw = text.encode('utf-16-le')
    for i in range(0, len(raw), 2):
        scan = int.from_bytes(raw[i:i+2], 'little')
        records = (Input * 2)(Input(1, InputData(key=Keyboard(0, scan, 4, 0, 0))),
                              Input(1, InputData(key=Keyboard(0, scan, 6, 0, 0))))
        if user.SendInput(2, records, ct.sizeof(Input)) != 2:
            raise RuntimeError('Windows rejected text input')


class WinOcr:
    def __enter__(self):
        script = Path(__file__).with_name('windows_ocr.ps1')
        self.proc = subprocess.Popen(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-File', str(script)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.responses = queue.Queue()
        threading.Thread(target=self.read, daemon=True).start()
        return self

    def read(self):
        for line in self.proc.stdout:
            self.responses.put(line)
        self.responses.put(None)

    def __exit__(self, *_):
        self.proc.stdin.close()
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                pass  # A delayed OCR exit must not replace the controller's actual error.

    def words(self, screenshot):
        buffer = io.BytesIO()
        screenshot.save(buffer, format='PNG')
        try:
            self.proc.stdin.write(base64.b64encode(buffer.getvalue()).decode('ascii') + '\n')
            self.proc.stdin.flush()
            result = self.responses.get(timeout=20)
        except (OSError, queue.Empty) as error:
            raise RuntimeError('The local OCR helper stopped responding') from error
        if not result:
            raise RuntimeError('The local OCR helper exited unexpectedly')
        payload = json.loads(result)
        if 'error' in payload:
            raise RuntimeError(payload['error'])
        return payload['lines']


def interactive_desktop():
    user.OpenInputDesktop.argtypes = (wt.DWORD,wt.BOOL,wt.DWORD)
    user.OpenInputDesktop.restype = wt.HANDLE
    user.CloseDesktop.argtypes = (wt.HANDLE,)
    user.GetUserObjectInformationW.argtypes = (wt.HANDLE,ct.c_int,wt.LPVOID,wt.DWORD,ct.POINTER(wt.DWORD))
    desktop=user.OpenInputDesktop(0,False,1)
    if not desktop:
        return False
    try:
        name=ct.create_unicode_buffer(64)
        required=wt.DWORD()
        return bool(user.GetUserObjectInformationW(desktop,2,name,ct.sizeof(name),ct.byref(required))
                    and name.value.casefold()=='default')
    finally:
        user.CloseDesktop(desktop)


class Screen:
    def __init__(self, window, recognizer):
        self.window = window
        self.bounds = bounds = rect(window)
        if not (140 <= bounds.w <= 3800 and 75 <= bounds.h <= 2500):
            raise RuntimeError('CapCut window is outside the qualified screen geometry')
        if user.GetForegroundWindow() != window:
            raise RuntimeError('CapCut lost the foreground before its screen could be verified')
        if user.IsHungAppWindow(window):
            raise RuntimeError('CapCut is not responding')
        image = capture_window(window, bounds)
        if user.GetForegroundWindow() != window or rect(window) != bounds:
            raise RuntimeError('CapCut moved or lost the foreground during inspection')
        self.lines = recognizer.words(image)
        self.image = image
        self.recognizer = recognizer

    def enlarged_matches(self,phrase,area,scale=2):
        """Read small controls in a bounded higher-resolution crop."""
        patch = self.image.crop((area.x,area.y,area.x+area.w,area.y+area.h))
        lines = self.recognizer.words(patch.resize((area.w*scale,area.h*scale)))
        saved = self.lines
        self.lines = [[dict(w, x=area.x+w['x']/scale, y=area.y+w['y']/scale,
                           w=w['w']/scale, h=w['h']/scale) for w in line] for line in lines]
        try:
            return self.matches(phrase,area=area)
        finally:
            self.lines = saved

    def button(self,phrase,area,scale=2):
        matches=self.enlarged_matches(phrase,area,scale)
        if len(matches)!=1:
            raise RuntimeError(f'Unable to uniquely verify CapCut control {phrase!r} ({len(matches)} matches)')
        return matches[0]

    def matches(self, phrase, *, area=None, prefix=False):
        needle = canonical(phrase)
        found = {}
        for line in self.lines:
            words = [w for w in line if not area or
                     area.x <= w['x'] + w['w']/2 < area.x + area.w and
                     area.y <= w['y'] + w['h']/2 < area.y + area.h]
            for start in range(len(words)):
                terms = ''
                for end in range(start, len(words)):
                    terms += canonical(words[end]['t'])
                    if terms == needle or (prefix and terms.startswith(needle)):
                        batch = words[start:end+1]
                        x = round(min(w['x'] for w in batch))
                        y = round(min(w['y'] for w in batch))
                        bounds = Box(x, y, round(max(w['x']+w['w'] for w in batch))-x,
                                     round(max(w['y']+w['h'] for w in batch))-y)
                        found[bounds] = None
                        break
                    if not needle.startswith(terms):
                        break
        return list(found)

    def unique(self, phrase, **rules):
        matches = self.matches(phrase, **rules)
        if len(matches) != 1:
            raise RuntimeError(f'Unable to uniquely verify CapCut control {phrase!r} ({len(matches)} matches)')
        return matches[0]

    def row_text(self, row):
        words = [w for line in self.lines for w in line
                 if abs(w['y'] + w['h']/2 - row.center[1]) <= max(row.h,10)]
        return ' '.join(w['t'] for w in sorted(words, key=lambda w: w['x']))

    def check(self):
        if not interactive_desktop():
            raise RuntimeError('The Windows input desktop is locked or unavailable. No action sent.')
        if user.GetForegroundWindow() != self.window or rect(self.window) != self.bounds:
            raise RuntimeError('CapCut lost focus or moved before input. No action sent.')

    def visible_at(self, point):
        x, y = self.bounds.x + point[0], self.bounds.y + point[1]
        under = user.WindowFromPoint(wt.POINT(x, y))
        return user.GetAncestor(under, 2) == self.window

    def click(self, point, *, double=False):
        self.check()
        if not self.visible_at(point):
            raise RuntimeError(f'Another window obscures the CapCut control at {point}.')
        x, y = self.bounds.x + point[0], self.bounds.y + point[1]
        user.SetCursorPos(x, y)
        for _ in range(2 if double else 1):
            user.mouse_event(2, 0, 0, 0, 0)
            user.mouse_event(4, 0, 0, 0, 0)
            time.sleep(.075)

    def text(self, value):
        self.check()
        keys(0x11, 0x41)
        send_text(value)


def wait_for(fn, description, seconds=12, poll=.35):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = fn()
        if value:
            return value
        time.sleep(poll)
    raise RuntimeError(f'Timed out waiting for {description}')
