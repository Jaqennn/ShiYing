"""字幕：Whisper 识别英文（或任意语言）→ DeepL / 大模型翻译 → 中文 SRT。

识别引擎用 whisper.cpp 的 whisper-cli（独立 exe，CPU 版任何电脑都能跑，NVIDIA 显卡可用 GPU 版）。
和 yt-dlp 一样走子进程，模型放在 %LOCALAPPDATA%\\ShiYing\\models。

为了避开 Windows 上命令行参数里的中文路径问题：音频先转成 16kHz WAV 放到 models\\work，
调用 whisper-cli 时工作目录设为 models，模型和音频都用纯英文的相对路径。
"""
import html
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from pathlib import Path

import requests

from . import components
from .netutil import NO_WINDOW, github_proxy, kill_tree, resolve_proxy
from .paths import BIN_DIR, IS_WIN, LOCAL_DIR

MODELS_DIR = LOCAL_DIR / "models"
WORK_DIR = MODELS_DIR / "work"
WHISPER_DIR = BIN_DIR / "whisper"

MODELS = {
    "base.en": {"file": "ggml-base.en.bin", "size": 148_000_000, "label": "快速",
                "desc": "英文专用，速度最快，准确度一般", "lang": "en"},
    "small.en": {"file": "ggml-small.en.bin", "size": 488_000_000, "label": "均衡",
                 "desc": "英文专用，速度和准确度兼顾", "lang": "en"},
    "large-v3-turbo": {"file": "ggml-large-v3-turbo-q5_0.bin", "size": 574_000_000, "label": "精准",
                       "desc": "最准，支持多种语言；没有 NVIDIA 显卡时较慢", "lang": None},
}
MODEL_REPO = "ggerganov/whisper.cpp"
VAD = {"file": "ggml-silero-v6.2.0.bin", "repo": "ggml-org/whisper-vad", "size": 885_000}
HF = {"official": "https://huggingface.co", "mirror": "https://hf-mirror.com"}
# 注意：whisper.cpp 的 v1.9.x 正式版只有源码，Windows 安装包发布在 bXXXX 构建版里，
# 所以不能只看 releases/latest，要往前翻到第一个带安装包的版本。
WHISPER_API = "https://api.github.com/repos/ggml-org/whisper.cpp/releases?per_page=30"
# 安装包文件名（新旧两种命名都认；排除 arm64）
WHISPER_ASSETS = {
    "gpu": [r"^whisper-bin-win-cuda-12[\d.]*-x64\.zip$", r"^whisper-cublas-12[\d.]*-bin-x64\.zip$",
            r"^whisper-bin-win-cuda-11[\d.]*-x64\.zip$", r"^whisper-cublas-11[\d.]*-bin-x64\.zip$"],
    "cpu": [r"^whisper-blas-bin-x64\.zip$", r"^whisper-bin-x64\.zip$"],
}
# GitHub 接口用不了时的保底地址（已确认存在的版本）
WHISPER_FALLBACK = {
    "cpu": ("whisper-blas-bin-x64.zip", "https://github.com/ggml-org/whisper.cpp/releases/download/b5454/whisper-blas-bin-x64.zip", "b5454"),
    "gpu": ("whisper-bin-win-cuda-12.4.0-x64.zip", "https://github.com/ggml-org/whisper.cpp/releases/download/b5454/whisper-bin-win-cuda-12.4.0-x64.zip", "b5454"),
}


def pick_whisper_asset(releases, gpu):
    """在 releases 列表（新→旧）里找第一个带对应安装包的版本，返回 (文件名, 地址, 版本号) 或 None。"""
    pats = WHISPER_ASSETS["gpu" if gpu else "cpu"]
    for rel in releases or []:
        if rel.get("draft"):
            continue
        assets = {a.get("name", ""): a.get("browser_download_url", "") for a in rel.get("assets") or []}
        for pat in pats:
            for name, url in assets.items():
                if re.match(pat, name, re.I) and url:
                    return name, url, rel.get("tag_name", "")
    return None

STAGES = {
    "queued": "字幕排队中", "fetching": "获取平台字幕", "extracting": "提取音频",
    "recognizing": "识别语音", "translating": "翻译", "writing": "写入字幕",
}


class SubtitleError(Exception):
    pass


class Canceled(SubtitleError):
    pass


# ---------------- 引擎与模型 ----------------

def whisper_exe():
    for name in ("whisper-cli", "main"):
        p = WHISPER_DIR / (name + (".exe" if IS_WIN else ""))
        if p.exists():
            return str(p)
    return shutil.which("whisper-cli")


def has_nvidia():
    if shutil.which("nvidia-smi"):
        return True
    return IS_WIN and Path(r"C:\Windows\System32\nvidia-smi.exe").exists()


def engine_build():
    f = WHISPER_DIR / "BUILD.txt"
    try:
        return f.read_text("utf-8").strip()
    except OSError:
        return "已安装" if whisper_exe() else None


def model_path(key):
    return MODELS_DIR / MODELS[key]["file"]


class Assets:
    """识别引擎、模型、VAD 的下载状态（给设置页轮询）。"""

    def __init__(self, settings):
        self.settings = settings
        self.state = {}
        self.lock = threading.Lock()

    def _set(self, key, **kw):
        with self.lock:
            self.state.setdefault(key, {"status": "idle", "progress": 0.0, "message": ""}).update(kw)

    def _get(self, key):
        with self.lock:
            return dict(self.state.get(key) or {"status": "idle", "progress": 0.0, "message": ""})

    def status(self):
        build = engine_build() if whisper_exe() else None
        models = []
        for key, m in MODELS.items():
            p = model_path(key)
            models.append({"key": key, **{k: v for k, v in m.items() if k != "file"},
                           "installed": p.exists(), **self._get("model:" + key)})
        return {
            "engine": {"installed": bool(whisper_exe()), "build": build,
                       "gpu": bool(build and re.search(r"cublas|cuda", build, re.I)), **self._get("engine")},
            "nvidia": has_nvidia(),
            "models": models,
            "vad": {"installed": (MODELS_DIR / VAD["file"]).exists(), **self._get("vad")},
        }

    # ----- 下载 -----
    def _stream(self, key, url, dest: Path, proxy, expect_min=100_000):
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        s = requests.Session()
        s.trust_env = False
        if proxy:
            s.proxies = {"http": proxy, "https": proxy}
        with s.get(url, stream=True, timeout=30, headers={"User-Agent": "ShiYing"}) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or 0)
            done, last = 0, 0.0
            with open(part, "wb") as f:
                for chunk in r.iter_content(512 * 1024):
                    f.write(chunk)
                    done += len(chunk)
                    if time.time() - last > 0.3:
                        last = time.time()
                        mb = done / 1048576
                        self._set(key, progress=(done / total) if total else 0.0,
                                  message=f"{mb:.0f} / {total / 1048576:.0f} MB" if total else f"{mb:.0f} MB")
        if done < expect_min:
            part.unlink(missing_ok=True)
            raise SubtitleError("下载的文件不完整")
        part.replace(dest)

    def _hf_download(self, key, repo, filename, dest, expect_min):
        src = self.settings.get("hf_source", "auto")
        order = {"official": ["official"], "mirror": ["mirror"]}.get(src, ["mirror", "official"])
        errors = []
        for host in order:
            url = f"{HF[host]}/{repo}/resolve/main/{filename}"
            proxy = github_proxy(self.settings) if host == "official" else None
            try:
                self._set(key, message="连接 " + ("hf-mirror.com" if host == "mirror" else "huggingface.co") + "…")
                self._stream(key, url, dest, proxy, expect_min)
                return
            except Exception as e:
                errors.append(f"{host}: {e.__class__.__name__}")
        raise SubtitleError("模型下载失败（" + "；".join(errors) + "）。检查网络，或手动下载后放进模型文件夹")

    def install_model_async(self, key):
        if key not in MODELS:
            return False
        if self._get("model:" + key)["status"] == "working":
            return True
        self._set("model:" + key, status="working", progress=0.0, message="准备下载…")

        def run():
            try:
                self._hf_download("model:" + key, MODEL_REPO, MODELS[key]["file"], model_path(key), 10_000_000)
                if not (MODELS_DIR / VAD["file"]).exists():   # 顺带装上静音检测模型（不到 1MB）
                    try:
                        self._hf_download("vad", VAD["repo"], VAD["file"], MODELS_DIR / VAD["file"], 100_000)
                        self._set("vad", status="done", progress=1.0, message="")
                    except Exception:
                        pass
                self._set("model:" + key, status="done", progress=1.0, message="已下载")
            except Exception as e:
                self._set("model:" + key, status="error", message=str(e)[:200])

        threading.Thread(target=run, daemon=True).start()
        return True

    def install_engine_async(self, gpu: bool):
        if self._get("engine")["status"] == "working":
            return True
        self._set("engine", status="working", progress=0.0, message="查询最新版本…")
        threading.Thread(target=self._install_engine, args=(gpu,), daemon=True).start()
        return True

    def _pick_asset(self, gpu):
        proxy = github_proxy(self.settings)
        try:
            s = requests.Session()
            s.trust_env = False
            r = s.get(WHISPER_API, timeout=15, proxies={"http": proxy, "https": proxy} if proxy else None,
                      headers={"Accept": "application/vnd.github+json", "User-Agent": "ShiYing"})
            r.raise_for_status()
            hit = pick_whisper_asset(r.json(), gpu)
            if hit:
                return hit
        except Exception:
            pass
        return WHISPER_FALLBACK["gpu" if gpu else "cpu"]

    def _install_engine(self, gpu):
        try:
            if not IS_WIN:
                raise SubtitleError("自动安装只支持 Windows")
            name, url, tag = self._pick_asset(gpu)
            mirror = (self.settings["github_mirror"] or "").strip()
            if mirror and url.startswith("https://github.com"):
                url = mirror.rstrip("/") + "/" + url
            self._set("engine", message=f"下载 {name}（{tag}）…")
            BIN_DIR.mkdir(parents=True, exist_ok=True)
            tmp = BIN_DIR / "whisper-download.zip"
            proxy = None if mirror else github_proxy(self.settings)
            self._download_big(url, tmp, proxy)
            self._set("engine", message="解压中…")
            install_whisper_zip(tmp, WHISPER_DIR, f"{name} {tag}".strip())
            tmp.unlink(missing_ok=True)
            self._set("engine", status="done", progress=1.0, message=("GPU 版" if gpu else "CPU 版") + "已安装")
        except Exception as e:
            msg = str(e)
            if isinstance(e, (requests.ConnectionError, requests.Timeout)):
                msg = "连不上 GitHub：开代理，或在「组件」里填 GitHub 加速前缀"
            elif "404" in msg:
                msg = "GitHub 上找不到安装包（whisper.cpp 可能又改了发布方式），请把这条提示发给开发者"
            self._set("engine", status="error", message=msg[:200])

    def _download_big(self, url, dest, proxy):
        """多连接下载大文件（GPU 版 600+MB），失败时退回单连接。"""
        from . import fastdl
        meter = fastdl.Meter()
        last = [0.0]
        total = [0]

        def tick():
            if time.time() - last[0] < 0.3:
                return
            last[0] = time.time()
            sp = meter.speed()
            t = total[0]
            msg = f"{meter.done / 1048576:.0f} / {t / 1048576:.0f} MB" if t else f"{meter.done / 1048576:.0f} MB"
            if sp > 0:
                msg += f"　{sp / 1048576:.1f} MB/s"
            self._set("engine", progress=(meter.done / t) if t else 0.0, message=msg)

        try:
            known = fastdl.probe(url, {"User-Agent": "ShiYing"}, proxy)
            if known[0] and known[0] < 1_000_000:
                raise SubtitleError("下载的文件不完整")
            total[0] = known[0]
            fastdl.download(url, dest, headers={"User-Agent": "ShiYing"}, proxy=proxy, connections=8,
                            cancel=threading.Event(), on_progress=tick, meter=meter, known=known)
        except fastdl.FastDLError as e:
            if "HTTP 404" in str(e):
                raise SubtitleError("404")
            self._stream("engine", url, dest, proxy, 1_000_000)


def install_whisper_zip(zip_path: Path, target: Path, build: str):
    """先解压到临时目录、确认有 whisper-cli 再替换旧版本，装失败不会把能用的旧引擎删掉。"""
    staging = target.with_name(target.name + ".new")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:   # 压缩包里有 Release\ 之类的子目录，全部铺平
            for info in zf.infolist():
                if info.is_dir():
                    continue
                base = info.filename.replace("\\", "/").rsplit("/", 1)[-1]
                if base:
                    with zf.open(info) as src, open(staging / base, "wb") as dst:
                        shutil.copyfileobj(src, dst, 1 << 20)
        names = {p.name.lower() for p in staging.iterdir()}
        if not ({"whisper-cli.exe", "whisper-cli", "main.exe"} & names):
            raise SubtitleError("压缩包里没有 whisper-cli.exe，可能发布格式变了")
        (staging / "BUILD.txt").write_text(build, "utf-8")
        if target.exists():
            old = target.with_name(target.name + ".old")
            shutil.rmtree(old, ignore_errors=True)
            target.rename(old)
            shutil.rmtree(old, ignore_errors=True)
        staging.rename(target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


# ---------------- SRT / VTT ----------------

def ms_to_srt(ms):
    ms = max(0, int(ms))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def tidy(segs):
    """去空行、修正重叠和倒序时间、合并相邻重复（Whisper 偶尔会重复一句）。"""
    out = []
    for a, b, t in segs:
        t = re.sub(r"\s+", " ", (t or "")).strip()
        if not t:
            continue
        if out and out[-1][2] == t and a - out[-1][1] < 1500:
            out[-1] = (out[-1][0], max(out[-1][1], b), t)
            continue
        out.append((int(a), int(max(b, a + 300)), t))
    for i in range(len(out) - 1):
        a, b, t = out[i]
        if b > out[i + 1][0]:
            out[i] = (a, max(a + 300, out[i + 1][0]), t)
    return out


def write_srt(path: Path, segs, lines):
    with open(path, "w", encoding="utf-8-sig", newline="\r\n") as f:   # 带 BOM，老播放器也认中文
        for i, ((a, b, _), text) in enumerate(zip(segs, lines), 1):
            f.write(f"{i}\n{ms_to_srt(a)} --> {ms_to_srt(b)}\n{text.strip()}\n\n")


def parse_vtt(text):
    segs = []
    ts = re.compile(r"(\d{1,2}:)?(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{1,2}:)?(\d{2}):(\d{2})[.,](\d{3})")

    def to_ms(h, m, s, ms):
        return ((int(h[:-1]) if h else 0) * 3600 + int(m) * 60 + int(s)) * 1000 + int(ms)

    blocks = re.split(r"\r?\n\r?\n", text)
    for blk in blocks:
        lines = blk.strip().splitlines()
        for i, ln in enumerate(lines):
            m = ts.search(ln)
            if m:
                a = to_ms(*m.group(1, 2, 3, 4))
                b = to_ms(*m.group(5, 6, 7, 8))
                body = " ".join(lines[i + 1:])
                body = html.unescape(re.sub(r"<[^>]+>", "", body))
                segs.append((a, b, body))
                break
    return segs


# ---------------- 翻译 ----------------

def _xml_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _chunks(lines, max_lines, max_chars):
    cur, size = [], 0
    for i, t in enumerate(lines):
        if cur and (len(cur) >= max_lines or size + len(t) > max_chars):
            yield cur
            cur, size = [], 0
        cur.append(i)
        size += len(t)
    if cur:
        yield cur


class DeepL:
    def __init__(self, key, proxy=None):
        key = (key or "").strip()
        if not key:
            raise SubtitleError("还没填 DeepL 密钥：设置 → 字幕 → 翻译")
        self.key = key
        self.base = os.environ.get("SHIYING_DEEPL_URL") or (
            "https://api-free.deepl.com" if key.endswith(":fx") else "https://api.deepl.com")
        self.s = requests.Session()
        self.s.trust_env = False
        if proxy:
            self.s.proxies = {"http": proxy, "https": proxy}
        self.s.headers.update({"Authorization": f"DeepL-Auth-Key {key}", "User-Agent": "ShiYing"})
        self.target = "ZH-HANS"

    def _post(self, body):
        for attempt in range(5):
            try:
                r = self.s.post(self.base + "/v2/translate", json=body, timeout=60)
            except requests.RequestException as e:
                if attempt == 4:
                    raise SubtitleError(f"连不上 DeepL（{e.__class__.__name__}），可在设置里让翻译走代理")
                time.sleep(1 + attempt)
                continue
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 529, 500, 502, 503):
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 400 and self.target == "ZH-HANS" and "target_lang" in r.text:
                self.target = body["target_lang"] = "ZH"   # 老接口只认 ZH
                continue
            raise SubtitleError(_deepl_error(r))
        raise SubtitleError("DeepL 一直繁忙，请稍后再试")

    def usage(self):
        try:
            r = self.s.get(self.base + "/v2/usage", timeout=20)
        except requests.RequestException as e:
            raise SubtitleError(f"连不上 DeepL（{e.__class__.__name__}）")
        if r.status_code != 200:
            raise SubtitleError(_deepl_error(r))
        d = r.json()
        return {"used": d.get("character_count", 0), "limit": d.get("character_limit", 0)}

    def translate(self, lines, src, on_progress, cancel):
        need = sum(len(t) for t in lines)
        try:
            u = self.usage()
            if u["limit"] and u["limit"] - u["used"] < need:
                raise SubtitleError(f"DeepL 额度不够：需要约 {need:,} 字符，本月还剩 {u['limit'] - u['used']:,}")
        except SubtitleError as e:
            if "额度不够" in str(e) or "密钥" in str(e):
                raise
        out = [None] * len(lines)
        groups = list(_chunks(lines, 40, 6000))
        for gi, idx in enumerate(groups):
            if cancel.is_set():
                raise Canceled("已取消")
            # 用 <s> 标签包住每条字幕整体发送：DeepL 能看到上下文，又能按条对齐
            xml = " ".join(f'<s i="{i}">{_xml_escape(lines[i])}</s>' for i in idx)
            ctx = " ".join(lines[j] for j in range(max(0, idx[0] - 4), idx[0]))
            body = {"text": [xml], "target_lang": self.target, "tag_handling": "xml"}
            if src and src not in ("auto",):
                body["source_lang"] = src.split("-")[0].upper()
            if ctx:
                body["context"] = ctx
            res = self._post(body)
            text = (res.get("translations") or [{}])[0].get("text", "")
            for m in re.finditer(r'<s i="(\d+)">(.*?)</s>', text, re.S):
                i = int(m.group(1))
                if i in idx:
                    out[i] = html.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()
            missing = [i for i in idx if not out[i]]
            if missing:   # 标签被吞掉的少数几条：逐条再翻
                body = {"text": [lines[i] for i in missing], "target_lang": self.target}
                res = self._post(body)
                for i, tr in zip(missing, res.get("translations") or []):
                    out[i] = tr.get("text", "").strip()
            on_progress(gi + 1, len(groups))
        return [o or "" for o in out]


def _deepl_error(r):
    code = r.status_code
    if code == 403:
        return "DeepL 密钥不对或已失效"
    if code == 456:
        return "DeepL 本月额度用完了（免费版每月 50 万字符），换个密钥或下月再试"
    try:
        msg = r.json().get("message", "")
    except Exception:
        msg = r.text[:120]
    return f"DeepL 返回错误 {code}：{msg}"


class LLM:
    """OpenAI 兼容接口（DeepSeek、通义千问、Kimi、OpenAI 等）。按上下文成段翻译，口语更自然。"""

    SYSTEM = ("你是专业的影视字幕译者。把用户给出的字幕逐条翻译成自然、口语化的简体中文。"
              "要求：1) 条数必须和输入完全一致，一条对一条，不合并、不拆分、不遗漏；"
              "2) 一句话被拆在相邻几条里时，结合上下文翻译，让每条读起来通顺；"
              "3) 人名、品牌用常见译名，专有名词不确定时保留英文；4) 不加任何解释。"
              "只输出 JSON：{\"t\": [\"第1条译文\", \"第2条译文\", ...]}")

    def __init__(self, base_url, key, model, proxy=None):
        if not (base_url and key and model):
            raise SubtitleError("大模型翻译需要填写接口地址、密钥和模型名：设置 → 字幕 → 翻译")
        url = base_url.strip().rstrip("/")
        if not url.endswith("/chat/completions"):
            # https://api.deepseek.com → /v1/chat/completions；…/compatible-mode/v1 → /chat/completions
            url += "/chat/completions" if re.search(r"/v\d+$", url) else "/v1/chat/completions"
        self.url = url
        self.model = model
        self.s = requests.Session()
        self.s.trust_env = False
        if proxy:
            self.s.proxies = {"http": proxy, "https": proxy}
        self.s.headers.update({"Authorization": f"Bearer {key}", "Content-Type": "application/json"})

    def _ask(self, before, batch):
        payload = {
            "model": self.model, "temperature": 0.3,
            "messages": [{"role": "system", "content": self.SYSTEM},
                         {"role": "user", "content": json.dumps({"前文（只作参考，不用翻译）": before, "待翻译": batch},
                                                                ensure_ascii=False)}],
        }
        for attempt in range(4):
            try:
                r = self.s.post(self.url, json=payload, timeout=120)
            except requests.RequestException as e:
                if attempt == 3:
                    raise SubtitleError(f"连不上翻译接口（{e.__class__.__name__}）")
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code in (429, 500, 502, 503, 529):
                time.sleep(2 ** (attempt + 1))
                continue
            if r.status_code == 401:
                raise SubtitleError("大模型接口密钥不对")
            if r.status_code != 200:
                raise SubtitleError(f"翻译接口返回 {r.status_code}：{r.text[:150]}")
            content = r.json()["choices"][0]["message"]["content"]
            m = re.search(r"\{.*\}", content, re.S)
            if not m:
                return None
            try:
                arr = json.loads(m.group(0)).get("t")
            except Exception:
                return None
            if isinstance(arr, list) and len(arr) == len(batch):
                return [str(x).strip() for x in arr]
            return None
        raise SubtitleError("翻译接口一直繁忙，请稍后再试")

    def _translate_range(self, lines, idx, out, cancel):
        if cancel.is_set():
            raise Canceled("已取消")
        before = [lines[j] for j in range(max(0, idx[0] - 5), idx[0])]
        res = self._ask(before, [lines[i] for i in idx])
        if res is None and len(idx) > 1:   # 条数对不上：对半拆开重试
            mid = len(idx) // 2
            self._translate_range(lines, idx[:mid], out, cancel)
            self._translate_range(lines, idx[mid:], out, cancel)
            return
        for i, t in zip(idx, res or [lines[idx[0]]]):
            out[i] = t

    def translate(self, lines, src, on_progress, cancel):
        out = [""] * len(lines)
        groups = list(_chunks(lines, 30, 4000))
        for gi, idx in enumerate(groups):
            self._translate_range(lines, idx, out, cancel)
            on_progress(gi + 1, len(groups))
        return out

    def check(self):
        res = self._ask([], ["Hello, world."])
        if not res:
            raise SubtitleError("接口能连上，但返回格式不对，换个模型试试")
        return res[0]


def make_translator(settings):
    proxy = resolve_proxy(settings, "translate")
    if settings["sub_translator"] == "llm":
        return LLM(settings["llm_base_url"], settings["llm_key"], settings["llm_model"], proxy)
    return DeepL(settings["deepl_key"], proxy)


# ---------------- 流水线 ----------------

def _threads():
    return max(4, min(12, (os.cpu_count() or 8) // 2))


def _ffmpeg_wav(video: Path, wav: Path, cancel):
    ff = components.find("ffmpeg")
    if not ff:
        raise SubtitleError("缺少 ffmpeg：到「设置 → 组件」安装")
    proc = subprocess.Popen([ff, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video), "-vn",
                             "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    while proc.poll() is None:
        if cancel.wait(0.2):
            kill_tree(proc)
            raise Canceled("已取消")
    if proc.returncode != 0:
        err = proc.stderr.read()
        if "does not contain any stream" in err or "Output file #0 does not contain" in err:
            raise SubtitleError("这个文件里没有声音")
        raise SubtitleError("提取音频失败：" + err[-150:])


def recognize(video: Path, settings, on_stage, cancel):
    """返回 (segments[(ms_from, ms_to, text)], 语言代码)。"""
    exe = whisper_exe()
    if not exe:
        raise SubtitleError("还没安装识别引擎：设置 → 字幕 → 安装")
    key = settings["sub_model"] if settings["sub_model"] in MODELS else "large-v3-turbo"
    if not model_path(key).exists():
        raise SubtitleError(f"还没下载「{MODELS[key]['label']}」模型：设置 → 字幕")
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    job = uuid.uuid4().hex[:10]
    wav = WORK_DIR / f"{job}.wav"
    out_json = WORK_DIR / f"{job}.json"
    try:
        on_stage("extracting", None)
        _ffmpeg_wav(video, wav, cancel)
        args = [exe, "-m", MODELS[key]["file"], "-f", f"work/{job}.wav", "-oj", "-of", f"work/{job}",
                "-pp", "-t", str(_threads()), "-l", MODELS[key]["lang"] or settings.get("sub_language", "auto") or "auto",
                "-sns"]
        if settings.get("sub_vad", True) and (MODELS_DIR / VAD["file"]).exists():
            args += ["--vad", "-vm", VAD["file"]]
        on_stage("recognizing", 0.0)
        proc = subprocess.Popen(args, cwd=str(MODELS_DIR), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace", bufsize=1, creationflags=NO_WINDOW)
        stop = threading.Event()

        def watch():
            while not stop.is_set() and proc.poll() is None:
                if cancel.wait(0.3):
                    kill_tree(proc)
                    return
        threading.Thread(target=watch, daemon=True).start()
        tail = []
        for line in proc.stderr:
            m = re.search(r"progress\s*=\s*(\d+)%", line)
            if m:
                on_stage("recognizing", int(m.group(1)) / 100)
            else:
                tail = (tail + [line.strip()])[-15:]
        proc.wait()
        stop.set()
        if cancel.is_set():
            raise Canceled("已取消")
        if proc.returncode != 0 or not out_json.exists():
            msg = next((t for t in reversed(tail) if "error" in t.lower()), tail[-1] if tail else "")
            raise SubtitleError("识别失败：" + (msg or f"退出码 {proc.returncode}")[:200])
        data = json.loads(out_json.read_bytes().decode("utf-8", "replace"), strict=False)
        lang = (data.get("result") or {}).get("language") or "en"
        segs = [(t["offsets"]["from"], t["offsets"]["to"], t.get("text", "")) for t in data.get("transcription", [])]
        return tidy(segs), lang
    finally:
        for p in (wav, out_json):
            try:
                p.unlink()
            except OSError:
                pass


def platform_subs(url, platform, settings, cancel):
    """YouTube 有人工英文字幕时直接用（比识别更准、几乎不花时间）。"""
    from . import ytdlp
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    job = uuid.uuid4().hex[:10]
    args = ytdlp.base_args(platform, settings) + [
        "--skip-download", "--write-subs", "--sub-langs", "en.*,en", "--sub-format", "vtt/best",
        "-o", str(WORK_DIR / f"{job}.%(ext)s"), "--", url]
    try:
        subprocess.run(args, capture_output=True, timeout=90, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        return None
    files = sorted(WORK_DIR.glob(f"{job}.*.vtt"))
    try:
        if cancel.is_set() or not files:
            return None
        segs = tidy(parse_vtt(files[0].read_text("utf-8", errors="replace")))
        return segs if len(segs) >= 3 else None
    finally:
        for f in WORK_DIR.glob(f"{job}.*"):
            f.unlink(missing_ok=True)


def run(task, settings, on_update, cancel):
    """给一个已下载的视频生成字幕，返回生成的文件列表。"""
    video = Path(task.get("filepath") or "")
    if not video.is_file():
        raise SubtitleError("找不到视频文件，可能已被移动或删除")

    def stage(name, frac, extra=""):
        on_update({"sub_status": name, "sub_progress": frac if frac is not None else 0.0,
                   "sub_msg": STAGES.get(name, name) + extra})

    segs, lang, source = None, "en", "whisper"
    if settings.get("sub_prefer_platform", True) and task.get("platform") == "youtube" and task.get("url"):
        stage("fetching", None)
        segs = platform_subs(task["url"], "youtube", settings, cancel)
        if segs:
            source = "youtube"
    if cancel.is_set():
        raise Canceled("已取消")
    if not segs:
        segs, lang = recognize(video, settings, lambda s, f: stage(s, f, f" {int((f or 0) * 100)}%" if s == "recognizing" else ""), cancel)
    if not segs:
        raise SubtitleError("没识别出任何语音")

    src_lines = [t for _, _, t in segs]
    outs = settings["sub_outputs"]
    if lang.startswith("zh"):
        zh = src_lines   # 本来就是中文，不用翻译
    else:
        tr = make_translator(settings)
        stage("translating", 0.0)
        zh = tr.translate(src_lines, lang, lambda d, n: stage("translating", d / n, f" {d}/{n}"), cancel)

    stage("writing", 1.0)
    base = video.with_suffix("")
    files = []
    if outs.get("zh", True):
        p = Path(f"{base}.zh.srt")
        write_srt(p, segs, zh)
        files.append(str(p))
    if outs.get("bilingual", True) and not lang.startswith("zh"):
        p = Path(f"{base}.zh-en.srt")
        write_srt(p, segs, [f"{z}\n{e}" for z, e in zip(zh, src_lines)])
        files.append(str(p))
    if outs.get("en", False) and not lang.startswith("zh"):
        p = Path(f"{base}.{lang.split('-')[0]}.srt")
        write_srt(p, segs, src_lines)
        files.append(str(p))
    if not files:   # 一个都没勾时至少给中文
        p = Path(f"{base}.zh.srt")
        write_srt(p, segs, zh)
        files.append(str(p))
    return {"files": files, "source": source, "lang": lang, "count": len(segs)}


class SubtitleManager:
    """字幕任务队列：一次只跑一个（识别很吃 CPU/显卡）。"""

    def __init__(self, settings, tasks):
        self.settings = settings
        self.tasks = tasks
        self.queue = []
        self.cancels = {}
        self.cv = threading.Condition()
        threading.Thread(target=self._loop, daemon=True).start()

    def enqueue(self, tid):
        t = self.tasks.get(tid)
        if not t or t.get("status") != "done":
            raise SubtitleError("视频下载完成后才能生成字幕")
        if t.get("sub_status") in STAGES:
            return True
        with self.tasks.lock:
            t.update(sub_status="queued", sub_progress=0.0, sub_msg=STAGES["queued"], sub_error="")
        with self.cv:
            self.queue.append(tid)
            self.cv.notify()
        return True

    def cancel(self, tid):
        with self.cv:
            if tid in self.queue:
                self.queue.remove(tid)
                t = self.tasks.get(tid)
                if t:
                    with self.tasks.lock:
                        t.update(sub_status="", sub_msg="", sub_progress=0.0)
                return True
        ev = self.cancels.get(tid)
        if ev:
            ev.set()
        return True

    def _loop(self):
        while True:
            with self.cv:
                while not self.queue:
                    self.cv.wait()
                tid = self.queue.pop(0)
            t = self.tasks.get(tid)
            if not t:
                continue
            ev = threading.Event()
            self.cancels[tid] = ev

            def upd(d, t=t):
                with self.tasks.lock:
                    t.update(d)
            try:
                res = run(t, self.settings, upd, ev)
                upd({"sub_status": "done", "sub_progress": 1.0, "sub_files": res["files"],
                     "sub_msg": ("用 YouTube 原字幕翻译" if res["source"] == "youtube" else "字幕已生成") + f"（{res['count']} 条）",
                     "sub_error": ""})
            except Canceled:
                upd({"sub_status": "", "sub_progress": 0.0, "sub_msg": "", "sub_error": ""})
            except Exception as e:
                upd({"sub_status": "error", "sub_progress": 0.0, "sub_msg": "", "sub_error": str(e)[:300]})
            finally:
                self.cancels.pop(tid, None)
                self.tasks.save()
