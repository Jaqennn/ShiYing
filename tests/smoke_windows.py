"""在 Windows 上真的装一遍组件（GitHub Actions 发布前跑，平时不用）：python tests/smoke_windows.py

用一个临时的用户目录模拟第一次打开：按安装向导的流程装 yt-dlp、ffmpeg、deno 和 CPU 版识别引擎，
确认都能运行，并打印每个组件实际下载了多大、用了多久、官方校验值有没有查到。
"""
import os
import sys
import tempfile
import time
from pathlib import Path

for stream in (sys.stdout, sys.stderr):   # GitHub 的 Windows 机器默认 cp1252，打印中文会报错
    stream.reconfigure(encoding="utf-8")
TMP = Path(tempfile.mkdtemp(prefix="shiying-smoke-"))
os.environ["LOCALAPPDATA"] = str(TMP / "local")
os.environ["APPDATA"] = str(TMP / "roaming")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import components, feedback, fetch, subtitle  # noqa: E402
from app.components import ComponentManager  # noqa: E402
from app.paths import BIN_DIR  # noqa: E402
from app.settings import Settings  # noqa: E402
from app.setup import SetupManager  # noqa: E402


def main():
    print("组件目录", BIN_DIR)
    settings = Settings()
    comps = ComponentManager(settings)
    assets = subtitle.Assets(settings)
    mgr = SetupManager(settings, comps, assets)

    plan = mgr.plan()
    print("缺少", plan["missing"])
    assert plan["missing"] == ["yt-dlp", "ffmpeg", "deno"], plan["missing"]

    for name, meta in components.SOURCES.items():
        d = fetch.release_digest(meta["repo"], meta["tag"], meta["asset"])
        print(f"官方校验值 {name}: {d or '查不到'}")

    t0 = time.time()
    mgr.start(["yt-dlp", "ffmpeg", "deno", "engine"])
    last = 0
    while mgr.status()["status"] == "working":
        st = mgr.status()
        if time.time() - last > 5:
            last = time.time()
            print(f"  {st['done'] / 1048576:.0f} / {st['total'] / 1048576:.0f} MB，"
                  f"{st['speed'] / 1048576:.1f} MB/s，正在装 {st['current']}")
        time.sleep(0.5)
    st = mgr.status()
    print("结果", st["status"], f"用时 {time.time() - t0:.0f} 秒")
    for it in st["items"]:
        print(f"  {it['key']}: {it['status']}，{it['total']} 字节，{it['message']}")
    assert st["status"] == "done", st

    for name in components.SOURCES:
        v = components.version(name)
        print(f"版本 {name}: {v}")
        assert v and v not in ("无法运行", "未知版本"), (name, v)
    assert subtitle.whisper_exe(), "识别引擎没装上"
    print("识别引擎", subtitle.engine_build())
    assert comps.missing() == []

    print("—— 反馈页信息 ——")
    print(feedback.collect(comps.status(), assets.status())["text"])


if __name__ == "__main__":
    main()
