#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按省级分片抓取中国全部「普速正线」（OSM: usage=main + highspeed!=yes）。

与 fetch_highspeed.py 完全同构，只有两点不同：
  - 标签条件 highspeed="yes"  →  highspeed!="yes"
  - 输出目录 raw_hs/          →  raw_conv/

为什么要**自动分块**：普速数据密度远高于高铁，大省（河南实测 9355 way / 6.7MB）
整省一次查询必然超时或被限流。这里按 bbox 面积自动切成 2x2 小块分别请求后去重合并，
单块跨度控制在 5° 以内。

用法:
    python3 fetch_conventional.py                # 抓全部未完成的省
    python3 fetch_conventional.py 河南 山东      # 只抓指定省
    python3 fetch_conventional.py --force 河南   # 忽略缓存重抓

输出:
    raw_conv/<省>.json      合并后的 Overpass 返回
    raw_conv/summary.json   各省统计（条数、里程、线路名、分块数）
"""
import concurrent.futures as futures
import json
import math
import os
import sys
import threading
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "raw_conv")

MIRRORS = [
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
MAX_WORKERS = 3          # 普速 payload 大，并发压到 3 减少限流
_print_lock = threading.Lock()

# bbox 面积超过这个阈值（平方度）就自动切 2x2
SPLIT_AREA = 20.0

# 省级 bbox (south, west, north, east) = (minlat, minlon, maxlat, maxlon)
# 与 fetch_highspeed.py 保持一致，改一处请同步另一处
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
    "湖北": (29.00, 108.40, 33.30, 116.10),
    "湖南": (24.60, 108.80, 30.20, 114.30),
    "广东": (20.20, 109.40, 25.60, 117.30),
    "广西": (20.90, 104.40, 26.40, 112.10),
    "海南": (18.10, 108.60, 20.10, 111.00),
    "重庆": (28.10, 105.30, 32.20, 110.20),
    "四川": (26.10, 97.40, 34.30, 108.50),
    "贵州": (24.60, 103.60, 29.30, 109.60),
    "云南": (21.10, 97.50, 29.30, 106.20),
    "西藏": (26.80, 78.20, 36.50, 99.10),
    "陕西": (31.70, 105.50, 39.60, 111.20),
    "甘肃": (32.50, 92.30, 42.90, 108.70),
    "青海": (31.60, 89.40, 39.20, 103.10),
    "宁夏": (35.20, 104.30, 39.40, 107.70),
    "新疆": (34.30, 73.50, 49.20, 96.40),
    "中国台湾": (21.90, 119.50, 25.30, 122.10),
    "中国香港": (22.20, 113.80, 22.60, 114.50),
    "中国澳门": (22.10, 113.50, 22.30, 113.70),
}


def log(*a):
    with _print_lock:
        print(*a, flush=True)


def haversine_km(a, b):
    R = 6371.0
    t = math.pi / 180
    la1, lo1 = a["lat"], a["lon"]
    la2, lo2 = b["lat"], b["lon"]
    h = (math.sin((la2 - la1) * t / 2) ** 2
         + math.cos(la1 * t) * math.cos(la2 * t) * math.sin((lo2 - lo1) * t / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(h))


def fetch(query, timeout=200, rounds=3):
    data = urllib.parse.urlencode({"data": query}).encode()
    for r in range(rounds):
        for url in MIRRORS:
            try:
                req = urllib.request.Request(
                    url, data=data, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    body = resp.read().decode("utf-8")
                if body.strip().startswith("{"):
                    return json.loads(body)
            except Exception as e:
                log(f"      [{url.split('//')[1].split('/')[0]}] {type(e).__name__}")
            time.sleep(2)
        log(f"      第 {r + 1} 轮全镜像失败，等待 10s")
        time.sleep(10)
    return None


def build_query(bbox):
    s, w, n, e = bbox
    return (
        f'[out:json][timeout:180][bbox:{s},{w},{n},{e}];'
        'way["railway"="rail"]["usage"="main"]["highspeed"!="yes"];'
        'out tags geom;'
    )


def split_bbox(bbox):
    """面积超阈值则切 2x2，否则原样返回单块。"""
    s, w, n, e = bbox
    area = (n - s) * (e - w)
    if area <= SPLIT_AREA:
        return [bbox]
    mid_lat = (s + n) / 2
    mid_lon = (w + e) / 2
    return [
        (mid_lat, w, n, mid_lon),      # 西北
        (mid_lat, mid_lon, n, e),      # 东北
        (s, w, mid_lat, mid_lon),      # 西南
        (s, mid_lon, mid_lat, e),      # 东南
    ]


def grab(prov, force=False):
    cache = os.path.join(OUT_DIR, f"{prov}.json")
    if os.path.exists(cache) and not force:
        return prov, "cached", None
    t0 = time.time()
    blocks = split_bbox(PROVINCES[prov])
    elements, seen = [], set()
    for i, blk in enumerate(blocks):
        data = fetch(build_query(blk))
        if not data:
            log(f"    {prov} 第 {i + 1}/{len(blocks)} 块失败")
            continue
        for e in data.get("elements", []):
            eid = e.get("id")
            # 分块之间会有重叠区，按 way id 去重
            if e.get("type") == "way" and eid not in seen:
                seen.add(eid)
                elements.append(e)
        time.sleep(2)

    if not elements:
        return prov, "FAILED", None

    with open(cache, "w", encoding="utf-8") as f:
        json.dump({"elements": elements}, f, ensure_ascii=False)

    ways = [e for e in elements if e.get("geometry")]
    if not ways:
        # 无普速正线的省是正常返回，字段必须补全，否则汇总格式化 KeyError 会打挂线程池
        return prov, "empty", {
            "ways": 0, "km": 0, "names": {}, "blocks": len(blocks),
            "secs": round(time.time() - t0),
        }

    names, total_km = {}, 0.0
    for w in ways:
        nm = w.get("tags", {}).get("name") or "(无名)"
        g = w["geometry"]
        km = sum(haversine_km(g[i], g[i + 1]) for i in range(len(g) - 1))
        names[nm] = names.get(nm, 0.0) + km
        total_km += km
    info = {
        "ways": len(ways),
        "km": round(total_km),
        "names": {k: round(v) for k, v in sorted(names.items(), key=lambda x: -x[1])},
        "blocks": len(blocks),
        "secs": round(time.time() - t0),
    }
    return prov, "ok", info


def main():
    args = sys.argv[1:]
    force = "--force" in args
    args = [a for a in args if not a.startswith("--")]
    targets = args or list(PROVINCES.keys())
    unknown = [t for t in targets if t not in PROVINCES]
    if unknown:
        print("未知省份:", unknown)
        return
    os.makedirs(OUT_DIR, exist_ok=True)

    summary = {}
    sp = os.path.join(OUT_DIR, "summary.json")
    if os.path.exists(sp):
        summary = json.load(open(sp, encoding="utf-8"))

    todo = [t for t in targets if force or not os.path.exists(os.path.join(OUT_DIR, f"{t}.json"))]
    print(f"待抓取 {len(todo)} / {len(targets)} 个省（并发 {MAX_WORKERS}，大省自动 2x2 分块）\n")

    with futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(grab, p, force): p for p in todo}
        for f in futures.as_completed(futs):
            prov = futs[f]
            try:
                prov, status, info = f.result()
            except Exception as e:
                log(f"  {prov:<6} EXCEPTION {type(e).__name__}: {e}")
                continue
            if isinstance(info, dict):
                summary[prov] = info
                log(f"  {prov:<6} {status:<7} ways={info.get('ways', 0):>5} "
                    f"{info.get('km', 0):>6}km  {info.get('secs', 0):>3}s  "
                    f"{len(info.get('names', {}))} 条线路  分块{info.get('blocks', 1)}")
            else:
                log(f"  {prov:<6} {status}")

    with open(sp, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    ok = [k for k, v in summary.items() if v.get("ways")]
    tot = sum(v["ways"] for v in summary.values() if isinstance(v, dict))
    print(f"\n完成：{len(ok)} 个省有数据，累计 {tot} 条 way")


if __name__ == "__main__":
    main()
