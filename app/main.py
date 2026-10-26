"""程序入口：创建窗口，加载前端。"""
import os
import sys

from . import APP_NAME
from .api import Api
from .paths import WEB_DIR, ensure_dirs


def run():
    ensure_dirs()
    try:
        import webview
    except ImportError:
        print("缺少 pywebview，请先运行：pip install -r requirements.txt")
        sys.exit(1)

    api = Api()
    window = webview.create_window(
        APP_NAME,
        url=str(WEB_DIR / "index.html"),
        js_api=api,
        width=1120,
        height=760,
        min_size=(880, 620),
        background_color="#101114",
        text_select=False,
    )
    api.bind(window)
    debug = bool(os.environ.get("SHIYING_DEBUG"))
    gui = "edgechromium" if sys.platform == "win32" else None
    webview.start(gui=gui, debug=debug, private_mode=False)


if __name__ == "__main__":
    run()
