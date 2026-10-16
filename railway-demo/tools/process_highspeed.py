#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 fetch_highspeed.py 抓回的省级数据，按线路名自动分组、清洗成可渲染数据。

与 process_lines.py 的区别：
  - process_lines.py  用人工维护的 WANTED 候选名表去匹配
  - 本脚本            直接按 OSM 的 name 标签聚类，**不需要事先知道线路名**
                     （新线通车后也会自动出现）

处理流程:
  1. 读 raw_hs/*.json，按 way id 全局去重（跨省交界处的 way 会被两省重复返回）
  2. 按 name 聚类成线路
  3. stitch 串链 → merge_chains 合并 → Douglas-Peucker 简化 → WGS84→GCJ-02
  4. 按里程过滤掉碎片段，输出 highspeed_lines.js / .json

用法:
    python3 process_highspeed.py
    MIN_KM=50 python3 process_highspeed.py     # 只要 50km 以上的线
    SIMPLIFY_TOL=0.002 python3 process_highspeed.py
"""
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import process_lines as P  # 复用 stitch / merge_chains / simplify / 坐标转换

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(HERE, "raw_hs")
ROOT = os.path.dirname(HERE)
OUT_JSON = os.path.join(ROOT, "highspeed_lines.json")
OUT_JS = os.path.join(ROOT, "highspeed_lines.js")

# 低于该里程的线路视为站段/联络线碎片段，单独归类不进主线
MIN_KM = float(os.environ.get("MIN_KM", 25))
SIMPLIFY_TOL = float(os.environ.get("SIMPLIFY_TOL", 0.001))
MERGE_TOL = float(os.environ.get("MERGE_TOL", P.MERGE_TOL))
MIN_CHAIN = int(os.environ.get("MIN_CHAIN", P.MIN_CHAIN))

# 名字里含这些词的，通常是引线/联络线，不作为独立线路展示
LINK_WORDS = ("联络线", "联络", "疏解线", "联络线")

# 个别 way 在 OSM 里用了繁体+英文的长名（如跨境线路），
# 不归一化的话会裂成一条独立线路，和同名主线对不上
NAME_FIX = {
    "廣深港高速鐵路 Guangzhou–Shenzhen–Hong Kong High Speed Rail": "广深港高速线",
}

# 兜底规则：名字里出现英文/空格时，只保留前面的中文部分
import re as _re
_ZH_ONLY = _re.compile(r'^[\u4e00-\u9fff·（）()\-—]+')


def clean_name(name):
    if name in NAME_FIX:
        return NAME_FIX[name]
    m = _ZH_ONLY.match(name)
    if m and ' ' in name:
        return m.group(0)
    return name


def is_link(name):
    return any(w in name for w in LINK_WORDS)


def load_all():
    """返回 {name: [(pts, tags), ...]}，已按 way id 去重。"""
    by_name = defaultdict(list)
    seen_ids = set()
    files = sorted(f for f in os.listdir(RAW_DIR) if f.endswith(".json") and f != "summary.json")
    if not files:
        print(f"没有数据，先运行 fetch_highspeed.py（raw_hs 为空）")
        return None
    for fn in files:
        path = os.path.join(RAW_DIR, fn)
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception as e:
            print(f"  ! {fn} 读取失败: {e}")
            continue
        n_before = len(seen_ids)
        for e in data.get("elements", []):
            if e.get("type") != "way":
                continue
            wid = e.get("id")
            if wid in seen_ids:
                continue          # 跨省重复的 way
            geom = e.get("geometry") or []
            tags = e.get("tags", {})
            name = tags.get("name")
            if not name or len(geom) < 2:
                continue
            name = clean_name(name)   # 繁体/英文混杂名归一化，避免同一条线裂成两条
            seen_ids.add(wid)
            by_name[name].append(([(p["lat"], p["lon"]) for p in geom], tags))
        print(f"  {fn[:-5]:<8} 累计去重 way {len(seen_ids)} (+{len(seen_ids) - n_before})")
    return by_name


def build(name, items):
    """把某条线路的所有 geom 拼成 polylines，返回 dict 或 None。"""
    ways = [w for w, _ in items]
    tags = [t for _, t in items]
    kind, kinfo = P.classify(tags, ways)
    # 名字兜底：OSM 上「朝凌高速线」这类线路缺 maxspeed 标签会被误判成普速，
    # 但既然是从 highspeed=yes 捞出来的，名字带高铁特征词的一律按高铁渲染。
    # 注意：**城际**不在列表（见 process_conventional.py 的说明）——城际不属于高速铁路。
    if kind != "highspeed" and any(w in name for w in ("高速线", "高铁", "客专")):
        kind = "highspeed"
    chains = P.stitch(ways)
    merged = P.merge_chains([c for c in chains if len(c) >= 3], MERGE_TOL)
    main = [c for c in merged if len(c) >= MIN_CHAIN]
    # 把短引轨/支线并入其依附的干线，避免「完整干线 + 1km 引轨」被当成断裂的线
    if P.ABSORB_SPURS and main:
        main, _absorbed = P.absorb_spurs(main, chains, MERGE_TOL)
    paths, longest = [], 0.0
    for c in main:
        simp = P.simplify(c, SIMPLIFY_TOL)
        gcj = [P.wgs84_to_gcj02(a, b) for a, b in simp]
        coords = [[round(a, 5), round(b, 5)] for a, b in gcj]
        if len(coords) < 2:
            continue
        km = sum(P.haversine_m(coords[i], coords[i + 1])
                 for i in range(len(coords) - 1)) / 1000.0
        longest = max(longest, km)
        paths.append(coords)
    if not paths:
        return None
    # 各段里程之和（含上下行并行），用于判断数据规模
    all_km = 0.0
    for pl in paths:
        all_km += sum(P.haversine_m(pl[i], pl[i + 1]) for i in range(len(pl) - 1)) / 1000.0
    return {
        "name": name,
        "kind": kind,
        "polylines": paths,
        "lengthKm": round(longest),
        "totalKm": round(all_km),
        "points": sum(len(p) for p in paths),
        "isLink": is_link(name),
        "maxspeed": kinfo.get("maxspeed_top", 0),
        "gauge": kinfo.get("main_gauge", "1435"),
    }


def main():
    global SIMPLIFY_TOL
    SIMPLIFY_TOL = SIMPLIFY_TOL
    print(f"参数: MIN_KM={MIN_KM} SIMPLIFY_TOL={SIMPLIFY_TOL} "
          f"MERGE_TOL={MERGE_TOL} MIN_CHAIN={MIN_CHAIN}\n")
    by_name = load_all()
    if not by_name:
        return
    print(f"\n去重后 {sum(len(v) for v in by_name.values())} 条 way，"
          f"{len(by_name)} 个线路名\n")

    lines, links, dropped = [], [], []
    for name in sorted(by_name):
        r = build(name, by_name[name])
        if not r:
            dropped.append(name)
            continue
        (links if r["isLink"] else lines).append(r)

    lines.sort(key=lambda r: -r["lengthKm"])
    links.sort(key=lambda r: -r["lengthKm"])
    main = [r for r in lines if r["lengthKm"] >= MIN_KM]
    small = [r for r in lines if r["lengthKm"] < MIN_KM]

    print(f"{'线路':<20}{'类型':<13}{'主线km':>8}{'点':>7}")
    print("-" * 50)
    for r in main:
        print(f"{r['name']:<20}{r['kind']:<13}{r['lengthKm']:>8}{r['points']:>7}")
    print(f"\n主线 {len(main)} 条 | 联络/引线 {len(links)} 条 | "
          f"<{MIN_KM}km 碎片段 {len(small)} 条 | 无有效几何 {len(dropped)} 条")

    out = {
        "generated": "highspeed (OSM usage=main & highspeed=yes)",
        "crs": "GCJ-02",
        "lines": {r["name"]: {k: v for k, v in r.items() if k != "name"} for r in main + links},
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    with open(OUT_JS, "w", encoding="utf-8") as f:
        f.write("window.HSLINES=")
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")
    kb = os.path.getsize(OUT_JS) / 1024
    pts = sum(r["points"] for r in main + links)
    km = sum(r["lengthKm"] for r in main + links)
    print(f"\n输出 {OUT_JSON} / {OUT_JS}")
    print(f"  {len(main) + len(links)} 条线路, {pts} 点, 主线累计 {km} km, 文件 {kb:.0f} KB")


if __name__ == "__main__":
    main()
