#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取 YouTube 频道 RSS 更新，并探测可用的第三方前端入口。

只依赖 Python 标准库，GitHub Actions 上无需 pip install。
输出：
  data/videos.json  频道最新视频（含本地缩略图路径）
  data/status.json  入口探测结果（按延迟排序，可用的排前面）
  data/meta.json    仓库信息与更新时间
  thumbs/*.jpg      本地缩略图，避免页面依赖 i.ytimg.com
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
THUMBS = os.path.join(ROOT, "thumbs")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}

MAX_PER_CHANNEL = 12
MAX_INSTANCES = 26

# 用来实测实例能否提取播放流的固定视频（"Me at the zoo"，公开、无地区限制）
PROBE_VIDEO = "jNQXAC9IVRw"
# YouTube 匿名播放风控的典型报错关键字
BOT_MARKERS = (
    "sign in to confirm",
    "not a bot",
    "login_required",
    "sign in to continue",
    "bot check",
)
# 这些状态码说明实例关掉了 API 或拒绝云服务器 IP，不能据此判断播放能力
API_CLOSED_CODES = (401, 403, 404, 405, 429)

ATOM = "{http://www.w3.org/2005/Atom}"
YTNS = "{http://www.youtube.com/xml/schemas/2015}"
MEDIA = "{http://search.yahoo.com/mrss/}"


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_get(url, timeout=25):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def probe(url, timeout=12):
    """探测入口是否可达。HTTP 错误码也算可达（说明没被拦）。"""
    start = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"url": url, "ok": True, "ms": int((time.time() - start) * 1000), "code": r.status}
    except urllib.error.HTTPError as e:
        return {"url": url, "ok": True, "ms": int((time.time() - start) * 1000), "code": e.code}
    except Exception:
        return {"url": url, "ok": False, "ms": 0, "code": 0}


def classify(obj):
    """把实例返回的 JSON 归类为 ok / bot / err / unknown。"""
    if not isinstance(obj, dict):
        return "unknown", ""
    err = obj.get("error")
    if err:
        s = str(err).strip()
        low = s.lower()
        if any(m in low for m in BOT_MARKERS):
            return "bot", s[:180]
        return "err", s[:180]
    streams = (
        (obj.get("formatStreams") or [])
        + (obj.get("adaptiveFormats") or [])
        + (obj.get("videoStreams") or [])
        + (obj.get("audioStreams") or [])
    )
    if streams:
        return "ok", ""
    return "unknown", ""


def check_invidious(base):
    base = base.rstrip("/")
    last = ""
    for q in ("?fields=error,title,formatStreams,adaptiveFormats", ""):
        try:
            raw = http_get(base + "/api/v1/videos/" + PROBE_VIDEO + q, timeout=30)
        except urllib.error.HTTPError as e:
            if e.code in API_CLOSED_CODES:
                return "unknown", "API 未开放 (HTTP %s)" % e.code
            return "err", "HTTP %s" % e.code
        except Exception as e:
            last = type(e).__name__
            continue
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        kind, note = classify(obj)
        if kind != "unknown":
            return kind, note
    return "unknown", last


def piped_api_for(frontend, hint=None):
    if hint:
        return hint.rstrip("/")
    try:
        host = urllib.parse.urlsplit(frontend).netloc
    except Exception:
        return None
    if host.startswith("piped."):
        return "https://pipedapi." + host[len("piped."):]
    return None


def check_piped(frontend, api):
    if not api:
        return "unknown", "no api url"
    try:
        raw = http_get(api.rstrip("/") + "/streams/" + PROBE_VIDEO, timeout=30)
        return classify(json.loads(raw))
    except urllib.error.HTTPError as e:
        if e.code in API_CLOSED_CODES:
            return "unknown", "API 未开放 (HTTP %s)" % e.code
        return "err", "HTTP %s" % e.code
    except Exception as e:
        return "unknown", type(e).__name__


def ratio_verdict(item):
    """用官方实例清单自报的播放成功率做旁证。"""
    ratio = item.get("ratio")
    total = item.get("ratioTotal")
    if ratio is None or not total or total < 3:
        return None, ""
    succ = item.get("ratioSuccess") or 0
    txt = "官方统计 %d/%d 成功" % (succ, total)
    if ratio <= 0:
        return "bot", txt
    if ratio >= 1:
        return "ok", txt
    return None, txt


def check_one(item):
    """可达性 + 能否真正提取播放流。"""
    url = item["url"]
    r = probe(url)
    if not r["ok"]:
        play, note = "unknown", ""
    elif item.get("type") == "piped":
        play, note = check_piped(url, piped_api_for(url, item.get("api")))
    else:
        play, note = check_invidious(url)

    if play == "unknown":
        verdict, txt = ratio_verdict(item)
        if verdict:
            play = verdict
        if txt:
            note = txt

    r["type"] = item.get("type", "unknown")
    r["play"] = play
    r["note"] = note
    if item.get("ratio") is not None:
        r["ratio"] = item["ratio"]
        r["ratioTotal"] = item.get("ratioTotal")
        r["ratioSuccess"] = item.get("ratioSuccess")
    return r


def resolve_page(path):
    """打开 YouTube 上的任意频道页，抠出 channelId。"""
    try:
        html = http_get("https://www.youtube.com/" + str(path).lstrip("/"), timeout=25)
        html = html.decode("utf-8", "ignore")
    except Exception:
        return None
    for pat in (r'"channelId":"(UC[\w-]{20,})"', r"/channel/(UC[\w-]{20,})", r'"externalId":"(UC[\w-]{20,})"'):
        m = re.search(pat, html)
        if m:
            return m.group(1)
    return None


def resolve_handle(handle):
    if not handle.startswith("@"):
        handle = "@" + handle
    return resolve_page(handle)


def resolve_by_search(query):
    """按名字在 YouTube 里搜频道，返回 (channelId, 频道名)。"""
    url = ("https://www.youtube.com/results?search_query="
           + urllib.parse.quote(query) + "&sp=EgIQAg%3D%3D")
    try:
        html = http_get(url, timeout=25).decode("utf-8", "ignore")
    except Exception:
        return None, None
    m = re.search(r'"channelRenderer":\{"channelId":"(UC[\w-]{20,})","title":\{"(?:simpleText|runs)":'
                  r'(?:\[\{"text":)?"((?:[^"\\]|\\.)*)"', html)
    if m:
        cid = m.group(1)
        try:
            name = json.loads('"' + m.group(2) + '"')
        except Exception:
            name = None
        return cid, name
    m = re.search(r'"channelId":"(UC[\w-]{20,})"', html)
    return (m.group(1), None) if m else (None, None)


def to_channel_id(entry):
    cid = (entry.get("id") or "").strip()
    if cid.startswith("UC"):
        return cid
    raw = (entry.get("handle") or entry.get("url") or "").strip()
    if not raw:
        return None
    m = re.search(r"/channel/(UC[\w-]{20,})", raw)
    if m:
        return m.group(1)
    m = re.search(r"youtube\.com/(@[\w.\-]+)", raw)
    if m:
        return resolve_handle(m.group(1))
    m = re.search(r"youtube\.com/(?:c|user)/([\w.\-%]+)", raw)
    if m:
        return resolve_page(raw.split("youtube.com", 1)[1].lstrip("/"))
    if raw.startswith("@"):
        return resolve_handle(raw)
    if raw.startswith("UC") and len(raw) > 20:
        return raw
    return None


INNERTUBE_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
LIVE_SCAN = 3


def player_info(video_id):
    body = json.dumps({
        "context": {"client": {
            "clientName": "WEB",
            "clientVersion": "2.20250101.00.00",
            "hl": "zh-CN",
            "gl": "US",
        }},
        "videoId": video_id,
    }).encode()
    req = urllib.request.Request(
        "https://www.youtube.com/youtubei/v1/player?key=" + INNERTUBE_KEY,
        data=body,
        headers={"User-Agent": UA, "Content-Type": "application/json"},
    )
    try:
        return json.loads(urllib.request.urlopen(req, timeout=25).read())
    except Exception:
        return None


def oembed_title(video_id):
    try:
        o = json.loads(http_get(
            "https://www.youtube.com/oembed?url="
            + urllib.parse.quote("https://www.youtube.com/watch?v=" + video_id, safe="")
            + "&format=json", timeout=20))
        return (o.get("title") or "").strip()
    except Exception:
        return ""


def live_probe(video_ids):
    """在一组候选视频里找真正在直播的那个，返回 (info, 诊断字符串)。

    直播开始时就会出现在频道 RSS 的最新条目里，所以拿最新的几条逐个验即可。
    必须用 liveBroadcastDetails.isLiveNow：isLiveContent 对「已结束的直播回放」也是 true，
    只看它会误报。
    """
    diag = []
    for vid in video_ids:
        d = player_info(vid)
        if d is None:
            return None, "player api unreachable"
        vd = d.get("videoDetails") or {}
        lb = (((d.get("microformat") or {}).get("playerMicroformatRenderer") or {})
              .get("liveBroadcastDetails") or {})
        status = (d.get("playabilityStatus") or {}).get("status")
        diag.append("%s[%s,now=%s]" % (vid, status, lb.get("isLiveNow")))
        if lb.get("isLiveNow") is True or (vd.get("isLive") is True and not lb.get("endTimestamp")):
            title = (vd.get("title") or "").strip() or oembed_title(vid)
            return {"id": vid, "title": title or "（直播中）"}, " ".join(diag)
    return None, " ".join(diag)


def parse_feed(xml_bytes):
    root = ET.fromstring(xml_bytes)
    out = []
    for e in root.findall(ATOM + "entry"):
        vid = (e.findtext(YTNS + "videoId") or "").strip()
        if not vid:
            m = re.search(r"yt:video:([\w-]+)", e.findtext(ATOM + "id") or "")
            vid = m.group(1) if m else ""
        if not vid:
            continue
        title = (e.findtext(ATOM + "title") or "").strip()
        published = (e.findtext(ATOM + "published") or "").strip()
        author = (e.findtext(ATOM + "author/" + ATOM + "name") or "").strip()
        out.append({"id": vid, "title": title, "published": published, "author": author})
    return out


def save_thumb(vid):
    dest = os.path.join(THUMBS, vid + ".jpg")
    if os.path.exists(dest) and os.path.getsize(dest) > 1000:
        return True
    try:
        data = http_get("https://i.ytimg.com/vi/%s/hqdefault.jpg" % vid, timeout=20)
        with open(dest, "wb") as f:
            f.write(data)
        return True
    except Exception:
        return False


def discover_invidious():
    try:
        raw = json.loads(http_get("https://api.invidious.io/instances.json?sort_by=type,users", 25))
    except Exception:
        return []
    items = raw if isinstance(raw, list) else list(raw.items())
    out = []
    for item in items:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            name, meta = item
        elif isinstance(item, dict):
            name, meta = item.get("name"), item
        else:
            continue
        if not isinstance(meta, dict) or meta.get("type") != "https":
            continue
        uri = str(meta.get("uri") or name or "").strip()
        if not uri or uri.lower().endswith((".onion", ".i2p")):
            continue
        if not uri.startswith("http"):
            uri = "https://" + uri
        pb = ((meta.get("stats") or {}).get("playback") or {})
        out.append({
            "url": uri.rstrip("/"),
            "type": "invidious",
            "ratio": pb.get("ratio"),
            "ratioTotal": pb.get("totalRequests"),
            "ratioSuccess": pb.get("successfulRequests"),
        })
    return out


def dedup(instances):
    """按 URL 去重，同 URL 的条目互补字段（静态清单的 api 提示 + 官方清单的 ratio）。"""
    idx, out = {}, []
    for it in instances:
        u = str(it.get("url") or "").rstrip("/")
        if not u:
            continue
        if u in idx:
            tgt = out[idx[u]]
            for k, v in it.items():
                if tgt.get(k) in (None, "") and v not in (None, ""):
                    tgt[k] = v
            continue
        idx[u] = len(out)
        out.append(dict(it))
    return out


def main():
    os.makedirs(DATA, exist_ok=True)
    os.makedirs(THUMBS, exist_ok=True)

    with open(os.path.join(ROOT, "channels.json"), encoding="utf-8") as f:
        cfg = json.load(f)

    channels_out, keep_thumbs = [], set()
    for entry in cfg.get("channels", []):
        name = (entry.get("name") or "").strip()
        cid = to_channel_id(entry)
        if not cid and entry.get("search"):
            cid, found = resolve_by_search(entry["search"])
            if not name:
                name = found or entry["search"]
            print("search %r -> %s (%s)" % (entry["search"], cid, name))
        if not cid:
            print("skip (cannot resolve): %r" % entry, file=sys.stderr)
            continue
        try:
            xml = http_get("https://www.youtube.com/feeds/videos.xml?channel_id=" + cid, timeout=25)
        except Exception as e:
            print("feed failed %s: %s" % (cid, e), file=sys.stderr)
            continue
        videos = parse_feed(xml)[:MAX_PER_CHANNEL]
        if not name or name == "示例频道":
            m = re.search(r"<title>(.*?)</title>", xml.decode("utf-8", "ignore"), re.S)
            name = (m.group(1).strip() if m else cid)
        for v in videos:
            if save_thumb(v["id"]):
                keep_thumbs.add(v["id"] + ".jpg")
        channels_out.append({"id": cid, "name": name, "videos": videos})
        print("ok: %s (%d videos)" % (name, len(videos)))

    def find_live(ch):
        cands = [v["id"] for v in ch.get("videos", [])][:LIVE_SCAN]
        info, diag = live_probe(cands)
        if not info:
            print("  livecheck %s: none  %s" % (ch["name"], diag))
            return None
        info["channelId"] = ch["id"]
        info["channel"] = ch["name"]
        print("LIVE: %s -> %s %s" % (ch["name"], info["id"], info["title"]))
        return info

    live_out = []
    if channels_out:
        with ThreadPoolExecutor(max_workers=8) as ex:
            for r in ex.map(find_live, channels_out):
                if r:
                    live_out.append(r)
                    if save_thumb(r["id"]):
                        keep_thumbs.add(r["id"] + ".jpg")

    for fn in os.listdir(THUMBS):
        if fn not in keep_thumbs:
            try:
                os.remove(os.path.join(THUMBS, fn))
            except OSError:
                pass

    with open(os.path.join(DATA, "videos.json"), "w", encoding="utf-8") as f:
        json.dump({"updated": now_iso(), "live": live_out, "channels": channels_out},
                  f, ensure_ascii=False, indent=1)

    with open(os.path.join(ROOT, "instances.json"), encoding="utf-8") as f:
        static = json.load(f).get("instances", [])
    pool = dedup(list(static) + discover_invidious())[:MAX_INSTANCES]

    with ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(check_one, pool))

    play_rank = {"ok": 0, "unknown": 1, "err": 2, "bot": 3}
    results.sort(key=lambda r: (
        not r["ok"],
        play_rank.get(r["play"], 1),
        r["ms"] if r["ok"] else 999999,
        r["url"],
    ))

    with open(os.path.join(DATA, "status.json"), "w", encoding="utf-8") as f:
        json.dump({"updated": now_iso(), "instances": results}, f, ensure_ascii=False, indent=1)

    with open(os.path.join(DATA, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "updated": now_iso(),
                "repo": os.environ.get("GITHUB_REPOSITORY", ""),
                "server_url": os.environ.get("GITHUB_SERVER_URL", "https://github.com"),
            },
            f,
            ensure_ascii=False,
            indent=1,
        )

    ok = sum(1 for r in results if r["ok"])
    playable = sum(1 for r in results if r["play"] == "ok")
    botblock = sum(1 for r in results if r["play"] == "bot")
    print("done. channels=%d live=%d instances=%d reachable=%d playable=%d botblocked=%d"
          % (len(channels_out), len(live_out), len(results), ok, playable, botblock))
    for r in results:
        if r["ok"]:
            print("  %-9s %-44s %5dms  %s %s"
                  % (r["type"], r["url"], r["ms"], r["play"], r["note"]))


if __name__ == "__main__":
    main()
