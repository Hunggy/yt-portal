#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性诊断：从 GitHub Actions 的 IP 出发，测试几种判断「是否正在直播」的手段。
跑完即删。"""
import hashlib
import json
import re
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"

# 1-LpQekNa9g = Lofi Girl 24/7 直播；Fgm5kUWBB7c = KPL（可能已结束）
# jNQXAC9IVRw / 9HJrossmW1c = 普通视频
CANDS = [
    ("LofiGirl-24/7-live", "1-LpQekNa9g"),
    ("KPL-maybe-live", "Fgm5kUWBB7c"),
    ("zoo-VOD", "jNQXAC9IVRw"),
    ("normal-VOD", "9HJrossmW1c"),
]


def get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


print("========== A. 缩略图 hqdefault_live.jpg 是否可区分 ==========")
for name, vid in CANDS:
    row = [name]
    for suffix in ("hqdefault_live.jpg", "hqdefault.jpg"):
        try:
            st, body = get("https://i.ytimg.com/vi/%s/%s" % (vid, suffix))
            row.append("%s len=%d md5=%s" % (st, len(body), hashlib.md5(body).hexdigest()[:10]))
        except urllib.error.HTTPError as e:
            row.append("HTTP %s" % e.code)
        except Exception as e:
            row.append(type(e).__name__)
    print("  %-20s live[%s]  norm[%s]" % (row[0], row[1], row[2]))

print()
print("========== B. innertube player 各 client ==========")
CLIENTS = [
    {"clientName": "WEB", "clientVersion": "2.20250101.00.00"},
    {"clientName": "MWEB", "clientVersion": "2.20250101.00.00"},
    {"clientName": "WEB_EMBEDDED_PLAYER", "clientVersion": "1.20250101.00.00"},
    {"clientName": "TVHTML5_SIMPLY_EMBEDDED_PLAYER", "clientVersion": "2.0"},
    {"clientName": "ANDROID", "clientVersion": "19.09.37", "androidSdkVersion": 30},
]
for cl in CLIENTS:
    c = dict(cl)
    c.setdefault("hl", "zh-CN")
    c.setdefault("gl", "US")
    body = json.dumps({"context": {"client": c}, "videoId": "Fgm5kUWBB7c",
                       "contentCheckOk": True, "racyCheckOk": True}).encode()
    try:
        req = urllib.request.Request(
            "https://www.youtube.com/youtubei/v1/player?key=" + KEY,
            data=body, headers={"User-Agent": UA, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=25) as r:
            st, raw = r.status, r.read()
        d = json.loads(raw)
        lb = (((d.get("microformat") or {}).get("playerMicroformatRenderer") or {})
              .get("liveBroadcastDetails") or {})
        vd = d.get("videoDetails") or {}
        print("  %-32s HTTP %s status=%-16s isLiveNow=%-6s isLive=%-6s micro=%s"
              % (cl["clientName"], st, (d.get("playabilityStatus") or {}).get("status"),
                 lb.get("isLiveNow"), vd.get("isLive"), bool(d.get("microformat"))))
    except urllib.error.HTTPError as e:
        print("  %-32s HTTP %s %s" % (cl["clientName"], e.code, e.read()[:120]))
    except Exception as e:
        print("  %-32s ERR %s %s" % (cl["clientName"], type(e).__name__, e))

print()
print("========== C. 频道 /live 页 ==========")
for name, cid in [("KPL", "UCfzRiA1Z11IXqHAWm2x_5Yg"),
                  ("YouTube", "UCBR8-60-B28hp2BmDPdntcQ")]:
    try:
        st, raw = get("https://www.youtube.com/channel/%s/live" % cid)
        h = raw.decode("utf-8", "ignore")
        can = re.search(r'<link rel="canonical" href="([^"]+)"', h)
        print("  %-10s HTTP %s len=%d canonical=%s isLiveNow=%d LOGIN=%d"
              % (name, st, len(h), can.group(1) if can else "-",
                 h.count("isLiveNow"), h.count("LOGIN_REQUIRED")))
    except urllib.error.HTTPError as e:
        print("  %-10s HTTP %s" % (name, e.code))
    except Exception as e:
        print("  %-10s ERR %s" % (name, type(e).__name__))
