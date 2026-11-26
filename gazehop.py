"""GazeHop - switch keyboard focus to the monitor you are looking at.

Local only: webcam frames are processed in memory, nothing is sent anywhere.
The only download is the MediaPipe face model, fetched once on first run.
"""
import ctypes
import json
import os
import queue
import sys
import threading
import time
import urllib.request
from ctypes import wintypes

import cv2
import numpy as np
import win32api
import win32con
import win32gui
import win32process

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
CALIB_PATH = os.path.join(HERE, "calibration.json")
MODEL_PATH = os.path.join(HERE, "face_landmarker.task")
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
             "face_landmarker/float16/1/face_landmarker.task")

user32 = ctypes.windll.user32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # physical pixel coordinates
except Exception:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass

DEFAULTS = {
    "toggle_hotkey": "ctrl+alt+l",
    "calibrate_hotkey": "ctrl+alt+c",
    "camera_index": 0,
    "dwell_ms": 500,
    "switch_margin": 0.85,
    "move_cursor": False,
    "typing_pause_ms": 2000,
}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


# ----------------------------------------------------------------- monitors
def get_monitors():
    """Monitors sorted left-to-right, then top-to-bottom."""
    mons = []
    for hmon, _hdc, rect in win32api.EnumDisplayMonitors():
        mons.append({"h": int(hmon), "rect": list(rect)})
    mons.sort(key=lambda m: (m["rect"][0], m["rect"][1]))
    return mons


# ------------------------------------------------------------------ hotkeys
MODS = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}


def parse_hotkey(s):
    mods, vk = 0x4000, None  # MOD_NOREPEAT
    for part in s.lower().replace(" ", "").split("+"):
        if part in MODS:
            mods |= MODS[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        elif part.startswith("f") and part[1:].isdigit():
            vk = 0x70 + int(part[1:]) - 1
        else:
            raise ValueError("Unsupported hotkey part: " + part)
    if vk is None:
        raise ValueError("Hotkey has no key: " + s)
    return mods, vk


class HotkeyThread(threading.Thread):
    def __init__(self, bindings, out_q):
        super().__init__(daemon=True)
        self.bindings = bindings  # {event_name: hotkey_string}
        self.out_q = out_q
        self.tid = None
        self.failed = []

    def run(self):
        self.tid = win32api.GetCurrentThreadId()
        ids = {}
        for i, (name, spec) in enumerate(self.bindings.items(), start=1):
            try:
                mods, vk = parse_hotkey(spec)
                if user32.RegisterHotKey(None, i, mods, vk):
                    ids[i] = name
                else:
                    self.failed.append(spec)
            except ValueError:
                self.failed.append(spec)
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312 and msg.wParam in ids:  # WM_HOTKEY
                self.out_q.put(ids[msg.wParam])
        for i in ids:
            user32.UnregisterHotKey(None, i)

    def stop(self):
        if self.tid:
            user32.PostThreadMessageW(self.tid, 0x0012, 0, 0)  # WM_QUIT


# ------------------------------------------------------------ focus switching
SKIP_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
                "Windows.UI.Core.CoreWindow"}
MY_PID = os.getpid()


def _cloaked(hwnd):
    v = ctypes.c_int(0)
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), 14, ctypes.byref(v), ctypes.sizeof(v))
    except Exception:
        return False
    return v.value != 0


def _eligible(hwnd):
    if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
        return False
    if not win32gui.GetWindowText(hwnd):
        return False
    if win32gui.GetClassName(hwnd) in SKIP_CLASSES or _cloaked(hwnd):
        return False
    ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    if ex & win32con.WS_EX_TOOLWINDOW and not ex & win32con.WS_EX_APPWINDOW:
        return False
    if ex & win32con.WS_EX_NOACTIVATE:
        return False
    try:
        if win32process.GetWindowThreadProcessId(hwnd)[1] == MY_PID:
            return False
    except Exception:
        pass
    return True


def monitor_of(hwnd):
    return int(win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST))


def topmost_window_on(mon_h):
    found = []

    def cb(hwnd, _):
        if _eligible(hwnd) and monitor_of(hwnd) == mon_h:
            found.append(hwnd)
            return False
        return True

    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        pass  # EnumWindows raises when the callback stops enumeration
    return found[0] if found else None


def force_foreground(hwnd):
    """Give keyboard focus to hwnd, working around Windows' focus-steal rule."""
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    # A synthetic unassigned-key press lets this process change the foreground.
    user32.keybd_event(0xE8, 0, 0, 0)
    user32.keybd_event(0xE8, 0, 2, 0)
    fg = win32gui.GetForegroundWindow()
    me = win32api.GetCurrentThreadId()
    fg_tid = win32process.GetWindowThreadProcessId(fg)[0] if fg else me
    attached = False
    if fg_tid != me:
        attached = bool(user32.AttachThreadInput(me, fg_tid, True))
    try:
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    finally:
        if attached:
            user32.AttachThreadInput(me, fg_tid, False)
    return win32gui.GetForegroundWindow() == hwnd


def focus_monitor(mon, move_cursor=False):
    fg = win32gui.GetForegroundWindow()
    if fg and monitor_of(fg) == mon["h"] and _eligible(fg):
        return  # already there
    target = topmost_window_on(mon["h"])
    if target:
        force_foreground(target)
    if move_cursor:
        r = mon["rect"]
        win32api.SetCursorPos(((r[0] + r[2]) // 2, (r[1] + r[3]) // 2))


# ------------------------------------------------------------- typing guard
# Skips mouse buttons (below 0x08) and 0xE8, the key force_foreground() fakes.
TYPING_KEYS = tuple(k for k in range(0x08, 0xFF) if k != 0xE8)


class TypingGuard(threading.Thread):
    """Remembers when a key was last pressed, so focus never moves mid-typing.

    Only a timestamp is kept, never which key it was.
    """

    def __init__(self, pause_ms, active=lambda: True, key_state=None,
                 clock=time.monotonic):
        super().__init__(daemon=True)
        self.pause = pause_ms / 1000.0
        self.active = active
        self.key_state = key_state or user32.GetAsyncKeyState
        self.clock = clock
        self.last = None

    def poll(self):
        # 0x8000: key is down now. 0x0001: pressed since the previous check.
        if any(self.key_state(k) & 0x8001 for k in TYPING_KEYS):
            self.last = self.clock()

    def typing(self):
        return self.last is not None and self.clock() - self.last < self.pause

    def run(self):
        while True:
            if self.active():  # keys are not checked while GazeHop is off
                self.poll()
            time.sleep(0.02)


# --------------------------------------------------------- gaze features
def ensure_model():
    if os.path.exists(MODEL_PATH):
        return
    urllib.request.urlretrieve(MODEL_URL, MODEL_PATH + ".part")
    os.replace(MODEL_PATH + ".part", MODEL_PATH)


def _p(lm, i):
    return np.array([lm[i].x, lm[i].y])


def features(lm):
    """4 numbers describing where the face/eyes point (all roughly scale-free)."""
    nose, lc, rc = _p(lm, 1), _p(lm, 234), _p(lm, 454)
    top, chin = _p(lm, 10), _p(lm, 152)
    yaw = (nose[0] - lc[0]) / max(rc[0] - lc[0], 1e-6)
    pitch = (nose[1] - top[1]) / max(chin[1] - top[1], 1e-6)

    def eye(outer, inner, up, low, iris):
        o, i, u, l, c = (_p(lm, k) for k in (outer, inner, up, low, iris))
        ix = (c[0] - o[0]) / (i[0] - o[0] + 1e-6)
        iy = (c[1] - u[1]) / (l[1] - u[1] + 1e-6)
        return ix, iy

    lx, ly = eye(33, 133, 159, 145, 468)
    rx, ry = eye(263, 362, 386, 374, 473)
    return np.array([yaw, pitch, (lx + rx) / 2, (ly + ry) / 2])


def open_camera(preferred=0):
    """Try the configured index first, then others, with both Windows backends."""
    order = [preferred] + [i for i in range(4) if i != preferred]
    for backend in (cv2.CAP_MSMF, cv2.CAP_DSHOW):
        for idx in order:
            cap = cv2.VideoCapture(idx, backend)
            if cap.isOpened():
                ok, _ = cap.read()
                if ok:
                    return cap
            cap.release()
    return None


class Model:
    """Nearest-centroid classifier over calibration samples (diagonal Gaussian)."""

    def __init__(self, centroids, sigma, rects):
        self.centroids = np.array(centroids)
        self.sigma = np.array(sigma)
        self.rects = rects

    @classmethod
    def fit(cls, samples, rects):
        cents = [np.median(s, axis=0) for s in samples]
        resid = np.vstack([s - c for s, c in zip(samples, cents)])
        sigma = np.maximum(resid.std(axis=0), 1e-3)
        return cls([c.tolist() for c in cents], sigma.tolist(), rects)

    def distances(self, f):
        return np.sqrt((((f - self.centroids) / self.sigma) ** 2).sum(axis=1))

    def save(self):
        with open(CALIB_PATH, "w", encoding="utf-8") as f:
            json.dump({"centroids": self.centroids.tolist(),
                       "sigma": self.sigma.tolist(), "rects": self.rects}, f)

    @classmethod
    def load(cls, rects):
        try:
            with open(CALIB_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return None
        if d.get("rects") != rects:
            return None  # monitor layout changed -> recalibrate
        return cls(d["centroids"], d["sigma"], rects)


# ------------------------------------------------------------------ tracker
class Tracker(threading.Thread):
    def __init__(self, cfg, app):
        super().__init__(daemon=True)
        self.cfg, self.app = cfg, app
        self.enabled = False
        self.collecting = False
        self.calibrating = False
        self.buffer = []
        self.model = None
        self.quit = False
        self.cap = None
        self.landmarker = None
        self.guard = TypingGuard(cfg["typing_pause_ms"], lambda: self.enabled)

    def _open(self):
        import mediapipe as mp
        from mediapipe.tasks import python as mpp
        from mediapipe.tasks.python import vision
        ensure_model()
        self.mp = mp
        opts = vision.FaceLandmarkerOptions(
            base_options=mpp.BaseOptions(model_asset_path=MODEL_PATH),
            running_mode=vision.RunningMode.VIDEO, num_faces=1)
        self.landmarker = vision.FaceLandmarker.create_from_options(opts)
        self.cap = open_camera(self.cfg["camera_index"])
        if self.cap is None:
            raise RuntimeError("Could not open camera (tried MSMF and DirectShow, "
                               "indexes 0-3). Close other apps using it.")

    def _close(self):
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        if self.landmarker is not None:
            self.landmarker.close()
            self.landmarker = None

    def run(self):
        self.guard.start()
        cand, cand_since, current = None, 0.0, None
        recent = []
        t0 = time.time()
        while not self.quit:
            if not (self.enabled or self.collecting or self.calibrating):
                self._close()
                recent.clear()
                cand = current = None
                time.sleep(0.1)
                continue
            if self.cap is None:
                try:
                    self._open()
                except Exception as e:  # camera busy / model download failed
                    self.enabled = self.calibrating = self.collecting = False
                    self.app.notify("Error: %s" % e)
                    self.app.set_state(False)
                    continue
            ok, frame = self.cap.read()
            if not ok:
                time.sleep(0.05)
                continue
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb)
            res = self.landmarker.detect_for_video(img, int((time.time() - t0) * 1000))
            if not res.face_landmarks:
                cand = None
                time.sleep(0.03)
                continue
            f = features(res.face_landmarks[0])
            if self.collecting:
                self.buffer.append(f)
            if not self.enabled or self.model is None or self.calibrating:
                recent.clear()
                cand = current = None  # the screen count may differ next time
                time.sleep(0.03)
                continue
            recent.append(f)
            recent[:] = recent[-5:]
            fm = np.median(recent, axis=0)
            d = self.model.distances(fm)
            best = int(np.argmin(d))
            if current is None:
                current = best
            elif best != current and d[best] < d[current] * self.cfg["switch_margin"]:
                pass  # clearly better -> candidate below
            else:
                best = current
            if best == current or self.guard.typing():
                cand = None  # dwell restarts after typing stops
            else:
                if cand != best:
                    cand, cand_since = best, time.time()
                elif (time.time() - cand_since) * 1000 >= self.cfg["dwell_ms"]:
                    current, cand = best, None
                    focus_monitor(self.app.monitors[current], self.cfg["move_cursor"])
            time.sleep(0.03)
        self._close()


# ---------------------------------------------------------------------- app
class App:
    def __init__(self):
        import tkinter as tk
        self.tk = tk
        self.cfg = load_config()
        self.monitors = get_monitors()
        self.rects = [m["rect"] for m in self.monitors]
        self.events = queue.Queue()
        self.root = tk.Tk()
        self.root.withdraw()
        self.tracker = Tracker(self.cfg, self)
        self.tracker.model = Model.load(self.rects)
        self.icon = None
        self.hotkeys = HotkeyThread(
            {"toggle": self.cfg["toggle_hotkey"],
             "calibrate": self.cfg["calibrate_hotkey"]}, self.events)

    # tray ----------------------------------------------------------------
    def _icon_image(self, on):
        from PIL import Image, ImageDraw
        im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        ImageDraw.Draw(im).ellipse((6, 6, 58, 58), fill=(40, 190, 90) if on else (130, 130, 130))
        ImageDraw.Draw(im).ellipse((24, 24, 40, 40), fill=(255, 255, 255))
        return im

    def start_tray(self):
        import pystray
        menu = pystray.Menu(
            pystray.MenuItem("Toggle on/off (%s)" % self.cfg["toggle_hotkey"],
                             lambda *_: self.events.put("toggle")),
            pystray.MenuItem("Calibrate (%s)" % self.cfg["calibrate_hotkey"],
                             lambda *_: self.events.put("calibrate")),
            pystray.MenuItem("Quit", lambda *_: self.events.put("quit")))
        self.icon = pystray.Icon("gazehop", self._icon_image(False),
                                 "GazeHop: OFF", menu)
        self.icon.run_detached()

    def set_state(self, on):
        if self.icon:
            self.icon.icon = self._icon_image(on)
            self.icon.title = "GazeHop: " + ("ON" if on else "OFF")

    def notify(self, msg):
        if self.icon:
            try:
                self.icon.notify(msg, "GazeHop")
            except Exception:
                pass

    # actions -------------------------------------------------------------
    def refresh_monitors(self):
        """Re-read the screens; the saved calibration only counts if they match."""
        self.monitors = get_monitors()
        self.rects = [m["rect"] for m in self.monitors]
        self.tracker.model = Model.load(self.rects)

    def turn_off(self):
        self.tracker.enabled = False
        self.set_state(False)

    def check_screens(self):
        if self.tracker.enabled and get_monitors() != self.monitors:
            self.turn_off()
            self.notify("Screens changed - OFF. Press %s to turn on again."
                        % self.cfg["toggle_hotkey"])

    def toggle(self):
        if self.tracker.calibrating:
            return
        if self.tracker.enabled:
            self.tracker.enabled = False
            self.set_state(False)
            self.notify("OFF - camera released")
            return
        self.refresh_monitors()
        if self.tracker.model is None:
            self.notify("Not calibrated yet - starting calibration")
            self.calibrate(then_enable=True)
            return
        self.tracker.enabled = True
        self.set_state(True)
        self.notify("ON")

    def calibrate(self, then_enable=False):
        if self.tracker.calibrating:
            return
        tk = self.tk
        self.refresh_monitors()
        if len(self.monitors) < 2:
            self.turn_off()
            self.notify("Only one monitor detected")
            return
        t = self.tracker
        t.enabled = False
        t.calibrating = True
        samples = []

        def show(i):
            if i >= len(self.monitors):
                finish()
                return
            r = self.rects[i]
            w = tk.Toplevel(self.root)
            w.overrideredirect(True)
            w.attributes("-topmost", True)
            w.geometry("%dx%d+%d+%d" % (r[2] - r[0], r[3] - r[1], r[0], r[1]))
            w.configure(bg="#101418")
            c = tk.Canvas(w, bg="#101418", highlightthickness=0)
            c.pack(fill="both", expand=True)
            cx, cy = (r[2] - r[0]) // 2, (r[3] - r[1]) // 2
            c.create_text(cx, cy - 120, fill="white", font=("Segoe UI", 28),
                          text="Screen %d of %d - look at the dot, head still"
                               % (i + 1, len(self.monitors)))
            c.create_oval(cx - 14, cy - 14, cx + 14, cy + 14, fill="#28be5a", outline="")
            w.update()
            self.root.after(1800, lambda: collect(i, w))

        def collect(i, w):
            t.buffer = []
            t.collecting = True
            self.root.after(3000, lambda: done(i, w))

        def done(i, w):
            t.collecting = False
            samples.append(np.array(t.buffer) if t.buffer else np.zeros((0, 4)))
            w.destroy()
            show(i + 1)

        def finish():
            t.calibrating = False
            if get_monitors() != self.monitors:
                self.notify("Screens changed during calibration - try again.")
                self.set_state(False)
                return
            if any(len(s) < 15 for s in samples):
                self.notify("Calibration failed - face not seen. Check camera.")
                self.set_state(False)
                return
            m = Model.fit(samples, self.rects)
            m.save()
            t.model = m
            if then_enable:
                t.enabled = True
                self.set_state(True)
                self.notify("Calibrated - ON")
            else:
                self.set_state(False)
                self.notify("Calibrated. Press %s to turn on." % self.cfg["toggle_hotkey"])

        show(0)

    def poll(self):
        try:
            while True:
                ev = self.events.get_nowait()
                if ev == "toggle":
                    self.toggle()
                elif ev == "calibrate":
                    self.calibrate()
                elif ev == "quit":
                    self.quit()
                    return
        except queue.Empty:
            pass
        self.check_screens()
        self.root.after(80, self.poll)

    def quit(self):
        self.tracker.quit = True
        self.tracker.enabled = False
        self.hotkeys.stop()
        if self.icon:
            self.icon.stop()
        self.root.quit()

    def run(self):
        self.tracker.start()
        self.hotkeys.start()
        self.start_tray()
        time.sleep(0.3)
        if self.hotkeys.failed:
            self.notify("Hotkey unavailable: " + ", ".join(self.hotkeys.failed))
        else:
            self.notify("Ready. %s toggles. %s calibrates."
                        % (self.cfg["toggle_hotkey"], self.cfg["calibrate_hotkey"]))
        self.root.after(80, self.poll)
        self.root.mainloop()


def selftest():
    print("monitors:", [m["rect"] for m in get_monitors()])
    for k in ("toggle_hotkey", "calibrate_hotkey"):
        print(k, parse_hotkey(load_config()[k]))
    ensure_model()
    print("model:", os.path.getsize(MODEL_PATH), "bytes")
    import mediapipe  # noqa: F401
    print("imports ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        App().run()
