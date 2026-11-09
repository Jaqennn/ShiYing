"""反馈页要显示的信息：系统版本、拾影和各组件的版本。用户点「复制」后一起发过来，查问题快很多。"""
import platform
import urllib.parse

from . import BUILD, __version__
from .paths import IS_WIN

REPO = "https://github.com/Jaqennn/ShiYing"
ISSUE_URL = REPO + "/issues/new?template=bug.yml"
# 腾讯问卷（国内不用翻墙、不用登录）。还没建好时留空，反馈页就只显示 GitHub 入口
SURVEY_URL = ""
OPEN_OK = (REPO, "https://wj.qq.com/")

_EDITIONS = {
    "Professional": "专业版", "Core": "家庭版", "CoreCountrySpecific": "家庭中文版", "CoreSingleLanguage": "家庭单语言版",
    "Enterprise": "企业版", "Education": "教育版", "ProfessionalWorkstation": "专业工作站版", "IoTEnterprise": "IoT 企业版",
}


def _reg(path, names):
    import winreg
    out = {}
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as k:
            for n in names:
                try:
                    out[n] = winreg.QueryValueEx(k, n)[0]
                except OSError:
                    pass
    except OSError:
        pass
    return out


def windows_label():
    """例如「Windows 11 专业版 24H2（26100.2314），64 位」。"""
    if not IS_WIN:
        return platform.platform()
    v = _reg(r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
             ["CurrentBuildNumber", "CurrentBuild", "UBR", "DisplayVersion", "ReleaseId", "EditionID"])
    try:
        build = int(v.get("CurrentBuildNumber") or v.get("CurrentBuild") or 0)
    except ValueError:
        build = 0
    name = "Windows 11" if build >= 22000 else "Windows 10" if build >= 10240 else "Windows " + platform.release()
    edition = _EDITIONS.get(v.get("EditionID", ""), v.get("EditionID", ""))
    ver = v.get("DisplayVersion") or v.get("ReleaseId") or ""
    num = f"{build}.{v['UBR']}" if build and "UBR" in v else (str(build) if build else "")
    machine = platform.machine().upper()
    bits = "64 位" if machine in ("AMD64", "X86_64") else "ARM64" if machine == "ARM64" else machine
    head = " ".join(x for x in (name, edition, ver) if x)
    return f"{head}（{num}），{bits}" if num else f"{head}，{bits}"


def webview2_version():
    if not IS_WIN:
        return ""
    key = r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    return _reg(key, ["pv"]).get("pv", "")


def collect(comps, assets, extra=None):
    """comps 是 ComponentManager.status() 的结果，assets 是 subtitle.Assets.status() 的结果。"""
    ver = {c["name"]: (c.get("version") or "未安装") for c in comps}
    eng = assets.get("engine") or {}
    if eng.get("installed"):
        engine = ("GPU 版" if eng.get("gpu") else "CPU 版") + (f"（{eng['build']}）" if eng.get("build") else "")
    else:
        engine = "未安装"
    models = "、".join(m["label"] for m in assets.get("models", []) if m.get("installed")) or "未下载"
    rows = [
        ("拾影", __version__ + (" " + BUILD if BUILD else "")),
        ("系统", windows_label()),
        ("yt-dlp", ver.get("yt-dlp", "未安装")),
        ("ffmpeg", ver.get("ffmpeg", "未安装")),
        ("deno", ver.get("deno", "未安装")),
        ("字幕引擎", engine),
        ("识别模型", models),
        ("显卡", "检测到 NVIDIA" if assets.get("nvidia") else "没检测到 NVIDIA"),
    ]
    wv = webview2_version()
    if wv:
        rows.append(("WebView2", wv))
    task = []
    if extra:
        for label, key in (("出错的网站", "site"), ("视频标题", "title"), ("错误提示", "error"), ("视频链接", "url")):
            val = str(extra.get(key) or "").strip()
            if val:
                task.append((label, val[:500]))
    text = "\n".join(f"{k}：{v}" for k, v in rows + task)
    return {
        "items": [{"label": k, "value": v} for k, v in rows],
        "task": [{"label": k, "value": v} for k, v in task],
        "text": text,
        "survey_url": SURVEY_URL,
        "issue_url": ISSUE_URL + "&" + urllib.parse.urlencode({"system": text[:600]}),
        "repo": REPO,
    }


def allowed_url(url: str) -> bool:
    return isinstance(url, str) and url.startswith(OPEN_OK)
