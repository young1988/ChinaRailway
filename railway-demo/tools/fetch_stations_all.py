#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按省级分片抓取中国全部铁路车站（OSM: railway=station / railway=halt）。

用途：支持用户手动录入任意车站名，需要一份覆盖全国的站名→坐标表。

为什么必须分片：全中国带 bbox 的正则扫描实测必超时（之前验证过）。
车站是 node，比 way 轻得多，单省通常几秒内返回。

关键过滤：
  - 剔除地铁/轻轨/有轨电车/单轨/缆车（station=subway、subway=yes 等）
  - 剔除无 name 的节点
  - 剔除国境外（省 bbox 会框进俄/蒙/朝/越/哈/印的车站）

用法:
    python3 fetch_stations_all.py              # 抓全部未完成的省
    python3 fetch_stations_all.py 北京 宁夏    # 只抓指定省
    python3 fetch_stations_all.py --force 江苏 # 忽略缓存重抓

输出:
    raw_st/<省>.json
"""
import concurrent.futures as futures
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "raw_st")

MIRRORS = [
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
MAX_WORKERS = 4
_print_lock = threading.Lock()

PROVINCES = {
    "北京": (39.40, 115.40, 41.10, 117.50),
    "天津": (38.50, 116.70, 40.30, 118.00),
    "河北": (36.00, 113.40, 42.60, 119.90),
    "山西": (34.50, 110.20, 40.80, 114.60),
    "内蒙古": (37.40, 97.20, 53.40, 126.10),
    "辽宁": (38.70, 118.80, 43.50, 125.80),
    "吉林": (40.90, 121.60, 46.30, 131.30),
    "黑龙江": (43.40, 121.20, 53.60, 135.10),
    "上海": (30.70, 120.90, 31.90, 122.00),
    "江苏": (30.70, 116.30, 35.10, 121.90),
    "浙江": (27.10, 118.40, 31.20, 123.00),
    "安徽": (29.40, 114.90, 34.70, 119.70),
    "福建": (23.50, 115.80, 28.40, 120.70),
    "江西": (24.50, 113.60, 30.10, 118.50),
    "山东": (34.30, 114.80, 38.40, 122.70),
    "河南": (31.40, 110.30, 36.40, 116.70),
    "湖北": (29.00, 108.30, 33.30, 116.10),
    "湖南": (24.60, 108.80, 30.20, 114.30),
    "广东": (20.20, 109.40, 25.60, 117.30),
    "广西": (20.90, 104.40, 26.40, 112.10),
    "海南": (18.10, 108.60, 20.20, 111.10),
    "重庆": (28.10, 105.30, 32.30, 110.20),
    "四川": (26.00, 97.50, 34.30, 108.60),
    "贵州": (24.60, 103.50, 29.30, 109.60),
    "云南": (21.10, 97.50, 29.30, 106.20),
    "西藏": (26.50, 78.20, 36.50, 99.10),
    "陕西": (31.70, 105.50, 39.60, 111.30),
    "甘肃": (32.50, 92.30, 42.90, 108.80),
    "青海": (31.60, 89.40, 39.30, 103.10),
    "宁夏": (35.20, 104.30, 39.40, 107.70),
    "新疆": (34.30, 73.50, 49.20, 96.40),
    "中国台湾": (21.90, 119.30, 25.40, 122.10),
    "中国香港": (22.15, 113.80, 22.60, 114.50),
    "中国澳门": (22.10, 113.50, 22.25, 113.65),
}

# 国境外车站：省 bbox 太粗会框进来，按轨距+非中文名+坐标三重过滤在清洗阶段做
QUERY = (
    '[out:json][timeout:180][bbox:{s},{w},{n},{e}];'
    'node["railway"~"^(station|halt)$"]["name"];'
    'out tags center;'
)

# 地铁 / 轻轨 / 有轨电车 / 单轨 / 缆车 —— 不属于国铁车站，必须剔除
URBAN_TAGS = {
    "subway": ("yes",),
    "light_rail": ("yes",),
    "tram": ("yes",),
    "monorail": ("yes",),
    "funicular": ("yes",),
    "station": ("subway", "light_rail", "tram", "monorail", "funicular"),
}


def log(msg):
    with _print_lock:
        print(msg, flush=True)


def is_urban(tags):
    for k, vals in URBAN_TAGS.items():
        if str(tags.get(k, "")).lower() in vals:
            return True
    nm = tags.get("name", "")
    if "地铁" in nm or "轻轨" in nm or "有轨电车" in nm:
        return True
    return False


def fetch(query, tries=3):
    data = urllib.parse.urlencode({"data": query}).encode()
    for attempt in range(tries):
        for url in MIRRORS:
            try:
                req = urllib.request.Request(
                    url, data=data, headers={"User-Agent": "Mozilla/5.0"}
                )
                return json.loads(urllib.request.urlopen(req, timeout=180).read().decode())
            except Exception:
                time.sleep(1.5)
        time.sleep(5 * (attempt + 1))
    return None


def do_province(prov):
    s, w, n, e = PROVINCES[prov]
    t0 = time.time()
    d = fetch(QUERY.format(s=s, w=w, n=n, e=e))
    if d is None:
        return prov, "FAIL", None
    els = d.get("elements", [])
    kept = []
    for x in els:
        if x.get("type") != "node":
            continue
        tg = x.get("tags", {})
        if not tg.get("name"):
            continue
        if is_urban(tg):
            continue
        kept.append(x)
    path = os.path.join(OUT_DIR, prov + ".json")
    json.dump({"elements": kept}, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    return prov, "OK", {
        "total": len(els),
        "kept": len(kept),
        "secs": round(time.time() - t0),
    }


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv
    os.makedirs(OUT_DIR, exist_ok=True)
    done = set()
    if not force:
        for f in os.listdir(OUT_DIR):
            if f.endswith(".json") and f != "summary.json":
                done.add(f[:-5])
    targets = args or [p for p in PROVINCES if p not in done]
    if not targets:
        log("全部省份已抓取完毕")
        return

    summary = {}
    with futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(do_province, p): p for p in targets}
        for f in futures.as_completed(futs):
            prov = futs[f]
            try:
                p, status, info = f.result()
            except Exception as ex2:          # 单省异常不能拖垮整批
                log("  %-6s EXCEPTION %s" % (prov, type(ex2).__name__))
                continue
            if isinstance(info, dict):
                summary[p] = info
                log("  %-6s %-5s 原始 %5d  保留 %5d  %3ds"
                    % (p, status, info.get("total", 0), info.get("kept", 0),
                       info.get("secs", 0)))
            else:
                log("  %-6s %s" % (p, status))

    sp = os.path.join(OUT_DIR, "summary.json")
    old = {}
    if os.path.exists(sp):
        try:
            old = json.load(open(sp, encoding="utf-8"))
        except Exception:
            old = {}
    old.update(summary)
    json.dump(old, open(sp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log("完成 %d 个省，累计车站 %d"
        % (len(old), sum(v.get("kept", 0) for v in old.values())))


if __name__ == "__main__":
    main()
