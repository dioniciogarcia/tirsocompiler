"""Tirso Compiler — desktop entry point.

Opens the program window. Run it with "Start Tirso Compiler.bat".
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api import Api  # noqa: E402
from app.core import paths  # noqa: E402

holder = {"window": None}


def emit(event, data):
    win = holder["window"]
    if not win:
        return
    try:
        win.evaluate_js("window.tirsoEvent && window.tirsoEvent(%s, %s)" % (json.dumps(event), json.dumps(data)))
    except Exception:
        pass


def main():
    import webview

    api = Api(emit=emit)
    ui = paths.app_root() / "ui" / "index.html"
    window = webview.create_window(
        "Tirso Compiler",
        url=str(ui),
        js_api=api,
        width=1380,
        height=880,
        min_size=(1120, 720),
        background_color="#0B0D12",
    )
    holder["window"] = window
    api.attach_window(window)

    def on_loaded():
        # Show a toast if the previous update failed
        from app.core import updater as updater_mod
        failed_msg = updater_mod.check_update_failed(paths.app_root().parent)
        if failed_msg:
            emit("update_progress", {"message": "⚠ Last update failed: " + failed_msg, "pct": 0})

        # Drag and drop: hand the full file paths to the interface, which
        # decides whether they are games (ROM Library) or BIOS files.
        try:
            from webview.dom import DOMEventHandler

            def on_drop(e):
                files = (e.get("dataTransfer") or {}).get("files") or []
                found = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
                if found:
                    emit("files_dropped", {"paths": found})

            window.dom.document.events.dragover += DOMEventHandler(lambda e: None, True, True)
            window.dom.document.events.drop += DOMEventHandler(on_drop, True, True)
        except Exception:
            pass  # drag and drop unavailable; the Add buttons still work

    window.events.loaded += on_loaded
    webview.start(debug=os.environ.get("TIRSO_DEBUG") == "1")


def show_crash(text):
    log = paths.logs_dir() / "startup-error.log"
    log.write_text(text, encoding="utf-8")
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None,
            "Tirso Compiler couldn't start.\n\nThe details were saved to:\n%s\n\n"
            "Send that file to Claude and it will be fixed." % log,
            "Tirso Compiler", 0x10)
    else:
        print(text, file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        show_crash(traceback.format_exc())
