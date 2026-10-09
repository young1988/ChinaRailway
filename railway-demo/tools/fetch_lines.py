#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 OSM(Overpass) 按线路名抓取中国铁路正线几何。

用法:
    python3 fetch_lines.py            # 抓取全部配置线路
    python3 fetch_lines.py 京沪 成昆   # 只抓指定关键字

输出:
    raw/<key>.json   每条线路的原始 Overpass 返回
    summary.json     抓取结果统计（条数、点数、经纬度范围）
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

MIRRORS = [
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

# 关键字 -> 期望的线路名（OSM 中的中文命名，实际以返回数据为准）
TARGETS = ["京沪", "京广", "沪昆", "京哈", "陇海", "京九", "兰新", "成昆", "杭深", "宁蓉"]

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw")


# 中国范围 bbox（south,west,north,east）——不加 bbox 时全球正则扫描会超时
BBOX = "18,73,54,136"


def fetch(query, timeout=90, tries=3):
    data = urllib.parse.urlencode({"data": query}).encode()
    for i in range(tries):
        for url in MIRRORS:
            try:
                req = urllib.request.Request(
                    url, data=data, headers={"User-Agent": "Mozilla/5.0"}
                )
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    body = r.read().decode("utf-8")
                if body.strip().startswith("{"):
                    return json.loads(body)
                print(f"    [{url.split('//')[1].split('/')[0]}] 非 JSON 响应，换镜像")
            except Exception as e:
                print(f"    [{url.split('//')[1].split('/')[0]}] {type(e).__name__}: {e}")
            time.sleep(2)
        print(f"    第 {i + 1} 轮全部镜像失败，8 秒后重试")
        time.sleep(8)
    return None


def build_queries(key):
    """按代价从低到高列出候选查询，逐个尝试。

    不加 usage=main 时 Overpass 要在中国范围内正则扫描全部轨道，
    实测必超时（12 分钟仍失败）。先抓正线，站场股道由
    fetch_station_tracks 单独按需补齐。
    """
    return [
        '[out:json][timeout:120]'
        f'[bbox:{BBOX}];'
        f'way["railway"="rail"]["name"~"^{key}"]["usage"="main"];'
        'out geom;',
        '[out:json][timeout:180]'
        f'[bbox:{BBOX}];'
        f'way["railway"="rail"]["name"~"^{key}"];'
        'out geom;',
    ]


def fetch_any(key, tries=3):
    for qi, query in enumerate(build_queries(key), 1):
        print(f"  查询变体 {qi} ...")
        data = fetch(query, tries=tries)
        if data and data.get("elements"):
            return data, qi
        print(f"  变体 {qi} 无数据，换下一个")
    return None, 0


def main():
    keys = sys.argv[1:] or TARGETS
    os.makedirs(OUT_DIR, exist_ok=True)
    summary = {}
    summary_path = os.path.join(OUT_DIR, "summary.json")
    # 已有文件不重复抓取，统计也不被本次运行覆盖
    if os.path.exists(summary_path):
        with open(summary_path, encoding="utf-8") as f:
            summary = json.load(f)

    for key in keys:
        cache = os.path.join(OUT_DIR, f"{key}.json")
        if os.path.exists(cache):
            print(f"\n=== {key} 已有缓存，跳过 ===")
            continue
        print(f"\n=== 抓取 {key} ===")
        data, qi = fetch_any(key)
        if not data:
            print(f"  {key}: 失败")
            summary[key] = {"ok": False}
            continue

        with open(cache, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

        ways = [e for e in data.get("elements", []) if e.get("type") == "way"]
        names = {}
        pts = 0
        for w in ways:
            n = w.get("tags", {}).get("name", "?")
            names[n] = names.get(n, 0) + 1
            pts += len(w.get("geometry", []))
        lats = [p["lat"] for w in ways for p in w.get("geometry", [])]
        lons = [p["lon"] for w in ways for p in w.get("geometry", [])]
        info = {
            "ok": True,
            "ways": len(ways),
            "points": pts,
            "names": names,
            "bbox": [round(min(lats), 3), round(min(lons), 3), round(max(lats), 3), round(max(lons), 3)] if lats else None,
        }
        summary[key] = info
        print(f"  ways={len(ways)} points={pts} names={names}")

    with open(os.path.join(OUT_DIR, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print("\n完成，统计写入 raw/summary.json")


if __name__ == "__main__":
    main()
