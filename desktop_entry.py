"""Entry point for the local macOS desktop application bundle."""
import signal
import sys
import tkinter as tk

from app import App
from app_paths import log_directory


def main():
    if getattr(sys, 'frozen', False):
        directory = log_directory()
        directory.mkdir(parents=True, exist_ok=True)
        stream = (directory / 'desktop.log').open('a', encoding='utf-8', buffering=1)
        sys.stdout = sys.stderr = stream
    root = tk.Tk()
    root.tk.call('tk', 'appname', 'MSU2 小屏控制器')
    ui = App(root)
    if sys.platform == 'darwin':
        # Route Command-Q through the same wait-for-write cleanup as the window.
        root.createcommand('tk::mac::Quit', ui.close)
    signal.signal(signal.SIGTERM, lambda *_: ui.close())

    def ready():
        root.deiconify()
        root.lift()
        root.focus_force()
        print(f"GUI_READY title={root.title()!r} mapped={root.winfo_ismapped()} "
              f"geometry={root.winfo_geometry()}", flush=True)

    def heartbeat():
        # Keep Python's signal handler responsive while Tk owns the event loop.
        if not ui.closing:
            root.after(200, heartbeat)

    root.after(200, ready)
    root.after(200, heartbeat)
    root.mainloop()


if __name__ == "__main__":
    if '--smoke-test' in sys.argv:
        from validate_bundle import main as smoke_test
        raise SystemExit(smoke_test())
    main()
