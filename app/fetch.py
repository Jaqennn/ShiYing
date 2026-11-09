"""组件和模型的下载：多连接加速、可随时停止、按官方校验值核对。

组件来自 GitHub。国内直连常常很慢，用户可以换成加速镜像；镜像是第三方提供的，
所以只要能从 GitHub 接口查到官方的 SHA-256，下完都会核对一遍，对不上就删掉、报错。
"""
import hashlib
import threading
from pathlib import Path

import requests

from . import fastdl

UA = {"User-Agent": "ShiYing"}
GH_API = "https://api.github.com/repos"


class Stopped(Exception):
    """用户点了停止，或者要换镜像重新下。"""


class FetchError(Exception):
    pass


def with_mirror(url: str, mirror: str) -> str:
    """GitHub 地址前面加上加速前缀，例如 https://ghfast.top/https://github.com/…"""
    mirror = (mirror or "").strip()
    if mirror and url.startswith("https://github.com/"):
        return mirror.rstrip("/") + "/" + url
    return url


def _session(proxy):
    s = requests.Session()
    s.trust_env = False
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    return s


def release_digest(repo: str, tag: str, asset: str, proxy=None, timeout=12):
    """从 GitHub 接口查某个发布文件的官方 SHA-256；查不到（接口连不上、旧发布没有校验值）就返回 None。"""
    path = "latest" if tag == "latest-release" else f"tags/{tag}"
    try:
        r = _session(proxy).get(f"{GH_API}/{repo}/releases/{path}", timeout=timeout,
                                headers={**UA, "Accept": "application/vnd.github+json"})
        if r.status_code != 200:
            return None
        return digest_of(r.json().get("assets") or [], asset)
    except (requests.RequestException, ValueError):
        return None


def digest_of(assets, name):
    for a in assets or []:
        if a.get("name") == name:
            d = a.get("digest") or ""
            if d.lower().startswith("sha256:") and len(d) == 71:
                return d[7:].lower()
    return None


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def download(url: str, dest: Path, *, proxy=None, stop: threading.Event, on_bytes=None,
             connections=8, expect_min=100_000, digest=None):
    """下载到 dest（先写 dest.part，下完再改名）。on_bytes(已下字节, 总字节, 每秒字节) 大约每 0.3 秒回调一次。

    多连接失败时换单连接再试一次；404 这类错误直接报。stop 置位后抛 Stopped。
    给了 digest（官方 SHA-256）就核对，对不上删掉文件并报错。
    """
    dest = Path(dest)
    last_err = None
    for conns in (connections, 1):
        meter = fastdl.Meter()
        total = 0

        def tick():
            if on_bytes:
                on_bytes(meter.done, total, meter.speed())

        try:
            known = fastdl.probe(url, UA, proxy)
            total = known[0]
            fastdl.download(url, dest, headers=UA, proxy=proxy, connections=conns, cancel=stop,
                            on_progress=tick, meter=meter, known=known)
            break
        except fastdl.Canceled:
            raise Stopped()
        except fastdl.FastDLError as e:
            if stop.is_set():
                raise Stopped()
            if "HTTP 404" in str(e) or "HTTP 403" in str(e):
                raise FetchError(str(e))
            last_err = e
        except requests.RequestException as e:
            if stop.is_set():
                raise Stopped()
            last_err = e
    else:
        raise last_err
    if on_bytes:
        size = dest.stat().st_size
        on_bytes(size, size, 0)
    if dest.stat().st_size < expect_min:
        dest.unlink(missing_ok=True)
        raise FetchError("下载的文件不完整")
    if digest and sha256(dest) != digest:
        dest.unlink(missing_ok=True)
        raise FetchError("文件校验没通过（和官方发布的不一致），已删除；换个下载方式再试")
    return dest
