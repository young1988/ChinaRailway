#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 fetch_conventional.py 抓回的省级普速数据，按线路名自动分组清洗。

与 process_highspeed.py 同构，差异在**体积控制**上：
普速路网密度是高铁的 2~3 倍（河南一省就 61914 个原始点），
沿用高铁的参数会产出 850KB+ 的文件。这里默认开更激进的压缩：

  - SIMPLIFY_TOL 0.0015 → 0.003（约 330m 容差；普速多长直段，视觉损失极小）
  - 坐标保留 5 位 → 4 位（约 11m 精度，远小于底图最小可视比例尺）
  - MIN_KM 25 → 40（普速里有大量几十公里的支线/专用线碎片段）

另外一个关键点：**排除已存在于 highspeed_lines.json 的线路名**，
避免同一条线在高铁层和普速层各画一遍（颜色打架、体积翻倍）。

用法:
    python3 process_conventional.py
    MIN_KM=60 SIMPLIFY_TOL=0.005 python3 process_conventional.py
"""
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import process_lines as P  # 复用 stitch / merge_chains / simplify / 坐标转换

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(HERE, "raw_conv")
ROOT = os.path.dirname(HERE)
OUT_JSON = os.path.join(ROOT, "conventional_lines.json")
OUT_JS = os.path.join(ROOT, "conventional_lines.js")
HS_JSON = os.path.join(ROOT, "highspeed_lines.json")

MIN_KM = float(os.environ.get("MIN_KM", 40))
SIMPLIFY_TOL = float(os.environ.get("SIMPLIFY_TOL", 0.003))
MERGE_TOL = float(os.environ.get("MERGE_TOL", P.MERGE_TOL))
MIN_CHAIN = int(os.environ.get("MIN_CHAIN", P.MIN_CHAIN))
# 坐标小数位：5 位约 1m，4 位约 11m。普速用 4 位，够用且省 8% 体积
NDIGITS = int(os.environ.get("NDIGITS", 4))

LINK_WORDS = ("联络线", "联络", "疏解线")

NAME_FIX = {
    "廣深港高速鐵路 Guangzhou–Shenzhen–Hong Kong High Speed Rail": "广深港高速线",
}

import re as _re
_ZH_ONLY = _re.compile(r'^[\u4e00-\u9fff·（）()\-—]+')
# 境外线路：俄/蒙的西里尔与拉丁转写、朝鲜谚文、越南语（拉丁字母）
_FOREIGN = _re.compile(r'[\u0400-\u04FF\uAC00-\uD7AF\u0100-\u024F]|[A-Za-z]')

# 俄蒙宽轨。中国台湾部分线路是 1067 窄轨，属中国境内，不在此列
_BAD_GAUGE = {"1520"}
# 联络线低于该里程就不输出（站场内的小连接线对展示无意义）
LINK_MIN_KM = float(os.environ.get("LINK_MIN_KM", 25))

# 名字里带这些词的，即便被 highspeed!=yes 捞出来也是高铁（OSM 漏标），
# 且很可能已经在高铁数据里了
HS_WORDS = ("高速线", "高铁", "客专", "城际")


def clean_name(name):
    if name in NAME_FIX:
        return NAME_FIX[name]
    m = _ZH_ONLY.match(name)
    if m and ' ' in name:
        return m.group(0)
    return name


def is_foreign(name):
    """省级 bbox 很粗（内蒙古框到了蒙古国、黑龙江框到了俄罗斯），
    会把境外铁路一起捞回来。名字里出现西里尔/谚文/拉丁字母的一律判定为境外。"""
    return bool(_FOREIGN.search(name))


def is_link(name):
    return any(w in name for w in LINK_WORDS)


def load_hs_names():
    """高铁数据里已有的线路名，普速这边要跳过，避免重复绘制。"""
    if not os.path.exists(HS_JSON):
        print(f"  ! 未找到 {HS_JSON}，跳过去重（会造成高铁/普速重叠绘制）")
        return set()
    d = json.load(open(HS_JSON, encoding="utf-8"))
    names = set(d.get("lines", {}).keys())
    print(f"  高铁数据已有 {len(names)} 条线路名，普速将跳过同名者")
    return names


def load_all(skip):
    """返回 {name: [(pts, tags), ...]}，已按 way id 去重。"""
    by_name = defaultdict(list)
    seen_ids = set()
    skipped = 0
    files = sorted(f for f in os.listdir(RAW_DIR) if f.endswith(".json") and f != "summary.json")
    if not files:
        print("没有数据，先运行 fetch_conventional.py（raw_conv 为空）")
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
            name = clean_name(name)
            if name in skip:
                skipped += 1
                continue
            seen_ids.add(wid)
            by_name[name].append(([(p["lat"], p["lon"]) for p in geom], tags))
        print(f"  {fn[:-5]:<8} 累计去重 way {len(seen_ids)} (+{len(seen_ids) - n_before})")
    if skipped:
        print(f"  跳过 {skipped} 条与高铁同名的 way")
    return by_name


def build(name, items):
    """把某条线路的所有 geom 拼成 polylines，返回 dict 或 None。"""
    ways = [w for w, _ in items]
    tags = [t for _, t in items]
    kind, kinfo = P.classify(tags, ways)
    # 名字兜底与高铁侧对称：既然是从 highspeed!=yes 捞出来的，
    # 名字带高铁特征词说明 OSM 漏标，仍按高铁渲染
    if kind != "highspeed" and any(w in name for w in HS_WORDS):
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
        coords = [[round(a, NDIGITS), round(b, NDIGITS)] for a, b in gcj]
        if len(coords) < 2:
            continue
        km = sum(P.haversine_m(coords[i], coords[i + 1])
                 for i in range(len(coords) - 1)) / 1000.0
        longest = max(longest, km)
        paths.append(coords)
    if not paths:
        return None
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
    print(f"参数: MIN_KM={MIN_KM} SIMPLIFY_TOL={SIMPLIFY_TOL} "
          f"MERGE_TOL={MERGE_TOL} MIN_CHAIN={MIN_CHAIN} NDIGITS={NDIGITS}\n")
    skip = load_hs_names()
    by_name = load_all(skip)
    if not by_name:
        return
    print(f"\n去重后 {sum(len(v) for v in by_name.values())} 条 way，"
          f"{len(by_name)} 个线路名\n")

    lines, links, dropped, foreign = [], [], [], []
    for name in sorted(by_name):
        r = build(name, by_name[name])
        if not r:
            dropped.append(name)
            continue
        # 境外宽轨（俄/蒙 1520mm）与境外语种名一律剔除
        if str(r["gauge"]) in _BAD_GAUGE:
            foreign.append(name)
            continue
        if is_foreign(name):
            foreign.append(name)
            continue
        (links if r["isLink"] else lines).append(r)

    lines.sort(key=lambda r: -r["lengthKm"])
    links.sort(key=lambda r: -r["lengthKm"])
    main = [r for r in lines if r["lengthKm"] >= MIN_KM]
    small = [r for r in lines if r["lengthKm"] < MIN_KM]
    # 联络线太多且多为站场短接线，只保留够长的
    links = [r for r in links if r["lengthKm"] >= LINK_MIN_KM]
    if foreign:
        print(f"  剔除境外线路 {len(foreign)} 条: {'、'.join(foreign[:8])}"
              f"{' …' if len(foreign) > 8 else ''}")

    print(f"{'线路':<20}{'类型':<13}{'主线km':>8}{'点':>7}")
    print("-" * 50)
    for r in main[:40]:
        print(f"{r['name']:<20}{r['kind']:<13}{r['lengthKm']:>8}{r['points']:>7}")
    if len(main) > 40:
        print(f"  ... 另有 {len(main) - 40} 条")
    print(f"\n主线 {len(main)} 条 | 联络/引线 {len(links)} 条 | "
          f"<{MIN_KM}km 碎片段 {len(small)} 条 | 无有效几何 {len(dropped)} 条")

    out = {
        "generated": "conventional (OSM usage=main & highspeed!=yes)",
        "crs": "GCJ-02",
        "lines": {r["name"]: {k: v for k, v in r.items() if k != "name"} for r in main + links},
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    with open(OUT_JS, "w", encoding="utf-8") as f:
        f.write("window.CONVLINES=")
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")
    kb = os.path.getsize(OUT_JS) / 1024
    pts = sum(r["points"] for r in main + links)
    km = sum(r["lengthKm"] for r in main + links)
    print(f"\n输出 {OUT_JSON} / {OUT_JS}")
    print(f"  {len(main) + len(links)} 条线路, {pts} 点, 主线累计 {km} km, 文件 {kb:.0f} KB")


if __name__ == "__main__":
    main()
