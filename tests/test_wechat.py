"""公众号解析的离线测试：用仿照真实页面结构的小片段，不联网。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import detect, wechat  # noqa: E402


def _fmt(fid, w, h, size, stem="0bc3abcdef"):
    # 和真实页面一样：数字写成 '1920' * 1，链接里的 & 写成 \x26amp;
    return f"""{{
        format_id: '{fid}' * 1,
        width: '{w}' * 1,
        height: '{h}' * 1,
        duration: '95' * 1,
        duration_ms: '95000' * 1,
        filesize: '{size}',
        url: 'http://mpvideo.qpic.cn/{stem}.f{fid}.mp4?dis_k=abc\\x26amp;dis_t=1793673084\\x26amp;auth_key=k{fid}',
        video_quality_level: '3' * 1,
        video_quality_wording: '超清',
    }},"""


def _entry(vid, formats, **flags):
    fl = "".join(f"{k}: '{v}' * 1,\n" for k, v in flags.items())
    return f"""{{
        video_id: '{vid}',
        is_mp_video: '1' * 1,
        is_mp_video_delete: '0' * 1,
        {fl}
        mp_video_trans_info: [ {''.join(formats)} ],
        cover_url: 'http://mmbiz.qpic.cn/cover_{vid}/0?wx_fmt=jpeg',
    }},"""


def _page(entries, content_vids=(), extra_html=""):
    iframes = "".join(
        f'<iframe class="video_iframe rich_pages" data-src="https://mp.weixin.qq.com/mp/readtemplate?'
        f't=pages/video_player_tmpl&amp;vid={v}" data-mpvid="{v}"></iframe>' for v in content_vids)
    return f"""<html><head>
<meta property="og:title" content="og 标题" />
</head><body><div class="rich_media_content" id="js_content">{iframes}{extra_html}</div>
<script>
var nickname = htmlDecode("测试公众号");
var msg_title = '标题 \\x26amp; 副标题'.html(false);
var msg_cdn_url = "http://mmbiz.qpic.cn/page_cover/0?wx_fmt=jpeg";
window.cgiData = {{ video_page_infos: [ {''.join(entries)} ], }};
</script></body></html>"""


ONE = _page([_entry("wxv_1", [
    _fmt("10002", 1920, 1080, 230071840),
    _fmt("10004", 854, 480, 53267105),
    _fmt("10102", 1080, 606, 63017060),
    _fmt("10104", 480, 270, 19483855),
])], ["wxv_1"])


def test_single_video_defaults_to_highest():
    r = wechat.parse_html(ONE, "https://mp.weixin.qq.com/s/AbCdEfGhIjk", compat=True)
    assert r["title"] == "标题 & 副标题"
    assert r["uploader"] == "测试公众号"
    assert r["count"] == 1 and r["kind"] == "video"
    assert r["id"] == "wxv_1"
    assert r["thumbnail"].startswith("https://mmbiz.qpic.cn/cover_wxv_1")
    keys = [q["key"] for q in r["qualities"]]
    # 兼容优先：只有 H.264 的两档，第一档就是最高
    assert keys == ["1080", "480", "audio_m4a"]
    assert r["qualities"][0]["size"] == 230071840
    v = r["_videos"][0]
    best = wechat.pick_format(v, r["_tiers"][0], True)
    assert best["format_id"] == "10002"
    assert "&dis_t=1793673084&" in best["url"] and "amp;" not in best["url"]


def test_hevc_shown_when_compat_off():
    r = wechat.parse_html(ONE, "https://mp.weixin.qq.com/s/x", compat=False)
    q = {x["key"]: x for x in r["qualities"]}
    assert list(q) == ["1080", "606", "480", "270", "audio_m4a"]
    assert q["606"]["sub"] == "H.265" and q["1080"]["sub"] == "最高"


def test_vertical_and_same_resolution():
    page = _page([_entry("wxv_9", [
        _fmt("10002", 432, 960, 6945528, "s9"), _fmt("10004", 432, 960, 4000000, "s9"),
        _fmt("10102", 432, 960, 3000000, "s9"),
    ])], ["wxv_9"])
    r = wechat.parse_html(page, "https://mp.weixin.qq.com/s/x", compat=False)
    assert r["vertical"] is True
    assert [q["key"] for q in r["qualities"]] == ["432", "audio_m4a"]
    # 同分辨率时选 H.264 里文件最大的那档
    assert wechat.pick_format(r["_videos"][0], 432, False)["format_id"] == "10002"


def test_multiple_videos_follow_article_order():
    page = _page([
        _entry("wxv_a", [_fmt("10002", 1280, 720, 100, "sa"), _fmt("10004", 854, 480, 50, "sa")]),
        _entry("wxv_b", [_fmt("10002", 1920, 1080, 300, "sb"), _fmt("10004", 854, 480, 70, "sb")]),
    ], ["wxv_b", "wxv_a"])
    r = wechat.parse_html(page, "https://mp.weixin.qq.com/s/MultiVideo01", compat=True)
    assert r["count"] == 2 and r["kind"] == "videos"
    assert [v["vid"] for v in r["_videos"]] == ["wxv_b", "wxv_a"]
    assert r["id"] == "MultiVideo01"
    q = {x["key"]: x for x in r["qualities"]}
    assert list(q) == ["1080", "720", "480", "audio_m4a"]
    assert q["1080"]["size"] == 300 + 100   # 第二个视频最高只有 720，就用 720
    assert q["480"]["size"] == 70 + 50


def test_duplicated_data_is_deduped():
    e = _entry("wxv_1", [_fmt("10002", 1920, 1080, 10), _fmt("10004", 854, 480, 5)])
    r = wechat.parse_html(_page([e, e], ["wxv_1"]), "https://mp.weixin.qq.com/s/x")
    assert r["count"] == 1
    assert len(r["_videos"][0]["formats"]) == 2


def test_deleted_video_reason():
    page = _page([_entry("wxv_1", [], is_mp_video_forbid=1)], ["wxv_1"])
    with pytest.raises(wechat.WechatError, match="违规"):
        wechat.parse_html(page, "https://mp.weixin.qq.com/s/x")


def test_blocked_account_message():
    page = """<html><body><div class="weui-msg"><div class="weui-msg__text-area">
      <h2 class="weui-msg__title tips">此账号已被屏蔽, 内容无法查看</h2>
      <p class="weui-msg__desc title">接相关投诉，此账号涉嫌违规，查看</p></div></div></body></html>"""
    with pytest.raises(wechat.WechatError, match="此账号已被屏蔽"):
        wechat.parse_html(page, "https://mp.weixin.qq.com/s/x")


def test_channels_and_tencent_video():
    snap = _page([], extra_html='<mp-common-videosnap class="js_uneditable" data-pluginname="mpvideosnap" data-type="video"></mp-common-videosnap>')
    with pytest.raises(wechat.WechatError, match="视频号"):
        wechat.parse_html(snap, "https://mp.weixin.qq.com/s/x")
    vqq = _page([], extra_html='<iframe class="video_iframe" data-src="https://v.qq.com/iframe/preview.html?vid=z0012abcd34&amp;width=500"></iframe>')
    with pytest.raises(wechat.TencentVideo) as ei:
        wechat.parse_html(vqq, "https://mp.weixin.qq.com/s/x")
    assert ei.value.urls == ["https://v.qq.com/x/page/z0012abcd34.html"]
    # 公众号视频和视频号混在一起：照常下载公众号视频，另外提示
    mixed = ONE.replace('<div class="rich_media_content" id="js_content">',
                        '<div class="rich_media_content" id="js_content"><mp-common-videosnap data-type="video"></mp-common-videosnap>')
    r = wechat.parse_html(mixed, "https://mp.weixin.qq.com/s/x")
    assert r["count"] == 1 and "视频号" in r["hint"]


def test_no_video():
    with pytest.raises(wechat.WechatError, match="没有找到视频"):
        wechat.parse_html(_page([]), "https://mp.weixin.qq.com/s/x")


def test_detect_wechat_links():
    cases = [
        ("https://mp.weixin.qq.com/s/Xy12AbCdEfGhIjKlMnOpQr", "https://mp.weixin.qq.com/s/Xy12AbCdEfGhIjKlMnOpQr"),
        ("【周末】去山里看银杏https://mp.weixin.qq.com/s/Xy12AbCdEfGhIjKlMnOpQr",
         "https://mp.weixin.qq.com/s/Xy12AbCdEfGhIjKlMnOpQr"),
        ("http://mp.weixin.qq.com/s?__biz=MzAwMDAwMDAwMA==&amp;mid=2247480000&amp;idx=4&amp;sn=abc#rd",
         "https://mp.weixin.qq.com/s?__biz=MzAwMDAwMDAwMA==&mid=2247480000&idx=4&sn=abc"),
        ("mp.weixin.qq.com/s/AbCdEfGh123", "https://mp.weixin.qq.com/s/AbCdEfGh123"),
    ]
    for text, url in cases:
        r = detect.extract(text)
        assert r == {"url": url, "platform": "wechat"}, (text, r)
    got = detect.extract_all("https://mp.weixin.qq.com/s/aaa https://b23.tv/AbC123d")
    assert [x["platform"] for x in got] == ["wechat", "bilibili"]
