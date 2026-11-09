"""程序入口：创建窗口，加载前端。"""
import json
import os
import sys
import time
from pathlib import Path

from . import APP_NAME, BUILD
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
        f"{APP_NAME} {BUILD}" if BUILD else APP_NAME,
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
    smoke = os.environ.get("SHIYING_SMOKE")
    if smoke:   # 只在自动测试里用：打开窗口、点一键安装、等装完，结果写进这个文件
        webview.start(_smoke, (window, api, smoke), gui=gui, debug=debug, private_mode=False)
    else:
        webview.start(gui=gui, debug=debug, private_mode=False)


def _smoke(window, api, out):
    res = {}

    def js(code):
        try:
            return window.evaluate_js(code)
        except Exception as e:
            return "JS 出错：" + repr(e)

    try:
        for _ in range(120):
            if js("document.readyState") == "complete":
                break
            time.sleep(0.5)
        time.sleep(5)
        res["opened"] = js("JSON.stringify({open: !document.getElementById('setupModal').hidden, "
                           "title: document.getElementById('setupTitle').textContent, "
                           "body: document.getElementById('setupBody').innerText.slice(0, 300)})")
        res["clicked"] = js("(function(){var b=document.getElementById('setupGo'); if(!b) return 'no button'; b.click(); return 'ok';})()")
        t0 = time.time()
        while time.time() - t0 < 900:
            st = api._setup.status()
            if st["status"] != "working" and (st["items"] or time.time() - t0 > 20):
                break
            time.sleep(2)
        res["job"] = api._setup.status()
        time.sleep(2)
        res["after"] = js("JSON.stringify({title: document.getElementById('setupTitle').textContent, "
                          "body: document.getElementById('setupBody').innerText.slice(0, 400)})")
        res["missing"] = api._comps.missing()
        res["feedback"] = api.feedback_info(None).get("data", {}).get("text")
    except Exception as e:
        res["error"] = repr(e)
    Path(out).write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), "utf-8")
    window.destroy()


if __name__ == "__main__":
    run()
