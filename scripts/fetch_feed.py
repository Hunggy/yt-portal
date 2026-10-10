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


def resolve_handle(handle):
    if not handle.startswith("@"):
        handle = "@" + handle
    try:
        html = http_get("https://www.youtube.com/" + handle, timeout=25).decode("utf-8", "ignore")
    except Exception:
        return None
    for pat in (r'"channelId":"(UC[\w-]{20,})"', r"/channel/(UC[\w-]{20,})", r'"externalId":"(UC[\w-]{20,})"'):
        m = re.search(pat, html)
        if m:
            return m.group(1)
    return None


def to_channel_id(entry):
    cid = (entry.get("id") or "").strip()
    if cid.startswith("UC"):
        return cid
    raw = (entry.get("handle") or entry.get("url") or entry.get("name") or "").strip()
    if not raw:
        return None
    m = re.search(r"/channel/(UC[\w-]{20,})", raw)
    if m:
        return m.group(1)
    m = re.search(r"youtube\.com/(@[\w.\-]+)", raw)
    if m:
        return resolve_handle(m.group(1))
    if raw.startswith("@"):
        return resolve_handle(raw)
    if raw.startswith("UC") and len(raw) > 20:
        return raw
    return None


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
        uri = str(name or meta.get("uri") or "").strip()
        if not uri or uri.lower().endswith((".onion", ".i2p")):
            continue
        if not uri.startswith("http"):
            uri = "https://" + uri
        out.append({"url": uri.rstrip("/"), "type": "invidious"})
    return out


def dedup(instances):
    seen, out = set(), []
    for it in instances:
        u = it["url"].rstrip("/")
        if u in seen:
            continue
        seen.add(u)
        out.append({"url": u, "type": it.get("type", "unknown")})
    return out


def main():
    os.makedirs(DATA, exist_ok=True)
    os.makedirs(THUMBS, exist_ok=True)

    with open(os.path.join(ROOT, "channels.json"), encoding="utf-8") as f:
        cfg = json.load(f)

    channels_out, keep_thumbs = [], set()
    for entry in cfg.get("channels", []):
        cid = to_channel_id(entry)
        if not cid:
            print("skip (cannot resolve): %r" % entry, file=sys.stderr)
            continue
        try:
            xml = http_get("https://www.youtube.com/feeds/videos.xml?channel_id=" + cid, timeout=25)
        except Exception as e:
            print("feed failed %s: %s" % (cid, e), file=sys.stderr)
            continue
        videos = parse_feed(xml)[:MAX_PER_CHANNEL]
        name = (entry.get("name") or "").strip()
        if not name or name == "示例频道":
            m = re.search(r"<title>(.*?)</title>", xml.decode("utf-8", "ignore"), re.S)
            name = (m.group(1).strip() if m else cid)
        for v in videos:
            if save_thumb(v["id"]):
                keep_thumbs.add(v["id"] + ".jpg")
        channels_out.append({"id": cid, "name": name, "videos": videos})
        print("ok: %s (%d videos)" % (name, len(videos)))

    for fn in os.listdir(THUMBS):
        if fn not in keep_thumbs:
            try:
                os.remove(os.path.join(THUMBS, fn))
            except OSError:
                pass

    with open(os.path.join(DATA, "videos.json"), "w", encoding="utf-8") as f:
        json.dump({"updated": now_iso(), "channels": channels_out}, f, ensure_ascii=False, indent=1)

    with open(os.path.join(ROOT, "instances.json"), encoding="utf-8") as f:
        static = json.load(f).get("instances", [])
    pool = dedup(list(static) + discover_invidious())[:MAX_INSTANCES]

    with ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(probe, [p["url"] for p in pool]))
    by_url = {p["url"]: p["type"] for p in pool}
    for r in results:
        r["type"] = by_url.get(r["url"], "unknown")
    results.sort(key=lambda r: (not r["ok"], r["ms"] if r["ok"] else 999999, r["url"]))

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

    print("done. channels=%d instances=%d ok=%d" % (len(channels_out), len(results), sum(1 for r in results if r["ok"])))


if __name__ == "__main__":
    main()
