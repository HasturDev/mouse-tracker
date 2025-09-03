# tracker_cli.py
# Windows-only. Python 3.9+ recommended.
import ctypes as C
from ctypes import wintypes as W
from ctypes import Structure
import sys
import time
import threading
import signal
import tkinter as tk

# ========== Win32 bindings (ctypes) ==========
user32 = C.windll.user32
shcore = None
try:
    shcore = C.windll.shcore
except Exception:
    pass

# Monitor flags
MONITOR_DEFAULTTONEAREST = 0x00000002
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = C.c_void_p(-4)

class POINT(Structure):  # use Structure, not W.Structure
    _fields_ = [("x", W.LONG), ("y", W.LONG)]

class RECT(Structure):
    _fields_ = [("left", W.LONG), ("top", W.LONG), ("right", W.LONG), ("bottom", W.LONG)]

# Prototypes
user32.GetCursorPos.argtypes = [C.POINTER(POINT)]
user32.GetCursorPos.restype  = W.BOOL

user32.GetWindowRect.argtypes = [W.HWND, C.POINTER(RECT)]
user32.GetWindowRect.restype  = W.BOOL

user32.GetClientRect.argtypes = [W.HWND, C.POINTER(RECT)]
user32.GetClientRect.restype  = W.BOOL

user32.ClientToScreen.argtypes = [W.HWND, C.POINTER(POINT)]
user32.ClientToScreen.restype  = W.BOOL

user32.ScreenToClient.argtypes = [W.HWND, C.POINTER(POINT)]
user32.ScreenToClient.restype  = W.BOOL

try:
    user32.GetDpiForWindow.argtypes = [W.HWND]
    user32.GetDpiForWindow.restype  = W.UINT
except Exception:
    pass

def enable_dpi_awareness():
    # Try Per-Monitor V2
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [C.c_void_p]
        user32.SetProcessDpiAwarenessContext.restype  = W.BOOL
        if user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
            return
    except Exception:
        pass
    # Fallback: PROCESS_PER_MONITOR_DPI_AWARE (2)
    try:
        if shcore:
            shcore.SetProcessDpiAwareness.argtypes = [W.INT]
            shcore.SetProcessDpiAwareness.restype  = W.HRESULT
            hr = shcore.SetProcessDpiAwareness(2)
            # OK (0) or already set (0x80070005)
            if hr in (0, 0x80070005):
                return
    except Exception:
        pass
    # Last resort (system DPI aware)
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass

def get_cursor_pos() -> tuple[int, int]:
    p = POINT()
    if not user32.GetCursorPos(C.byref(p)):
        raise OSError("GetCursorPos failed")
    return (p.x, p.y)

def get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    r = RECT()
    if not user32.GetWindowRect(W.HWND(hwnd), C.byref(r)):
        raise OSError("GetWindowRect failed")
    return (r.left, r.top, r.right, r.bottom)

def get_client_rect_screen(hwnd: int) -> tuple[int, int, int, int]:
    rc = RECT()
    if not user32.GetClientRect(W.HWND(hwnd), C.byref(rc)):
        raise OSError("GetClientRect failed")
    # client origin -> screen
    pt = POINT(0, 0)
    if not user32.ClientToScreen(W.HWND(hwnd), C.byref(pt)):
        raise OSError("ClientToScreen failed")
    left, top = pt.x, pt.y
    width  = rc.right - rc.left
    height = rc.bottom - rc.top
    return (left, top, left + width, top + height)

def screen_to_client(hwnd: int, x: int, y: int) -> tuple[int, int]:
    p = POINT(x, y)
    if not user32.ScreenToClient(W.HWND(hwnd), C.byref(p)):
        raise OSError("ScreenToClient failed")
    return (p.x, p.y)

def get_window_dpi(hwnd: int) -> int | None:
    try:
        return int(user32.GetDpiForWindow(W.HWND(hwnd)))
    except Exception:
        return None

# ========== App logic ==========
class TrackerApp:
    def __init__(self, interval_sec: float = 0.1):
        self.interval = max(0.01, float(interval_sec))
        self._stop = threading.Event()

        # Tk must run on the main thread
        self.root = tk.Tk()
        self.root.title("Tracker Window")
        self.root.geometry("400x240+200+200")  # WxH+X+Y
        # blank content
        frame = tk.Frame(self.root, bg="white")
        frame.pack(fill="both", expand=True)

        # Quit on ESC
        self.root.bind("<Escape>", lambda e: self.stop())

        # HWND for this window
        self.hwnd = None
        try:
            self.hwnd = int(self.root.winfo_id())
        except Exception:
            pass

        # Print a header once
        print("Tracking started. Press Esc in the window or Ctrl+C in the console to quit.", flush=True)
        if self.hwnd:
            dpi = get_window_dpi(self.hwnd)
            if dpi:
                print(f"Window DPI: {dpi}  (scale x{dpi/96.0:.2f})", flush=True)

        # Schedule first tick
        self.root.after(0, self._tick)

    def _tick(self):
        if self._stop.is_set():
            return

        try:
            # Ensure we have HWND (sometimes not ready immediately)
            if not self.hwnd:
                self.hwnd = int(self.root.winfo_id())

            # Gather positions
            mx, my = get_cursor_pos()
            wrect = get_window_rect(self.hwnd) if self.hwnd else (0, 0, 0, 0)
            crect = get_client_rect_screen(self.hwnd) if self.hwnd else (0, 0, 0, 0)

            # Mouse relative to client (clamped to show even when outside)
            try:
                relx, rely = screen_to_client(self.hwnd, mx, my) if self.hwnd else (0, 0)
            except OSError:
                relx, rely = (0, 0)

            # Format output
            # Example: t=12.345  mouse=(1200,640)  win=[100,100,500,340]  client=[108,132,492,332]  mouse_in_client=(12,20)
            now = time.perf_counter()
            line = (
                f"t={now:9.3f}  "
                f"mouse=({mx:5d},{my:5d})  "
                f"win=[{wrect[0]:5d},{wrect[1]:5d},{wrect[2]:5d},{wrect[3]:5d}]  "
                f"client=[{crect[0]:5d},{crect[1]:5d},{crect[2]:5d},{crect[3]:5d}]  "
                f"mouse_in_client=({relx:5d},{rely:5d})"
            )
            print(line, flush=True)

        except Exception as e:
            print(f"[error] {e}", file=sys.stderr, flush=True)

        # Reschedule
        self.root.after(int(self.interval * 1000), self._tick)

    def stop(self):
        self._stop.set()
        try:
            self.root.quit()
        except Exception:
            pass

    def run(self):
        self.root.mainloop()

def main():
    # Make Ctrl+C work nicely
    signal.signal(signal.SIGINT, lambda sig, frame: sys.exit(0))

    # High-DPI awareness for accurate pixel coords
    enable_dpi_awareness()

    # Optional: parse interval from CLI (e.g. python tracker_cli.py 0.05)
    interval = 0.1
    if len(sys.argv) > 1:
        try:
            interval = float(sys.argv[1])
        except ValueError:
            print("Usage: python tracker_cli.py [interval_seconds]", file=sys.stderr)

    app = TrackerApp(interval_sec=interval)
    app.run()

if __name__ == "__main__":
    main()
