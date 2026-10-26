"""路径约定：程序目录、数据目录、组件目录、前端目录。"""
import os
import sys
from pathlib import Path

from . import APP_ID

IS_WIN = sys.platform == "win32"
FROZEN = getattr(sys, "frozen", False)

# 程序所在目录（打包后是 exe 所在目录，开发时是项目根目录）
if FROZEN:
    APP_DIR = Path(sys.executable).resolve().parent
    RES_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
else:
    APP_DIR = Path(__file__).resolve().parent.parent
    RES_DIR = APP_DIR

WEB_DIR = RES_DIR / "web"

# 配置和下载记录（体积小）：%APPDATA%\ShiYing
if IS_WIN:
    DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / APP_ID
else:
    DATA_DIR = Path.home() / f".{APP_ID.lower()}"

# 组件和识别模型（体积大）：%LOCALAPPDATA%\ShiYing
# 放在固定位置而不是程序旁边：重新打包会清空 dist\ShiYing、换新版本会解压到新文件夹，放这里都不会丢，
# 源码运行（run.bat）和 exe 也共用同一份。
if IS_WIN:
    LOCAL_DIR = Path(os.environ.get("LOCALAPPDATA") or DATA_DIR.parent) / APP_ID
else:
    LOCAL_DIR = DATA_DIR
BIN_DIR = LOCAL_DIR / "bin"

# 也认这些位置的组件（只读，不往里装）：
# - 程序旁边的 bin：便携用法、手动放 exe、旧版本装的
# - 从源码打包出来的 dist\ShiYing：项目根目录的 bin
EXTRA_BIN_DIRS = [APP_DIR / "bin"]
if FROZEN and APP_DIR.parent.name.lower() == "dist":
    EXTRA_BIN_DIRS.append(APP_DIR.parent.parent / "bin")


def exe(name: str) -> str:
    return f"{name}.exe" if IS_WIN else name


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BIN_DIR.mkdir(parents=True, exist_ok=True)


def bin_dirs():
    return [BIN_DIR, *EXTRA_BIN_DIRS]


def default_download_dir() -> str:
    home = Path.home()
    for cand in (home / "Videos", home / "Downloads", home):
        if cand.exists():
            return str(cand / "拾影")
    return str(home / "拾影")
