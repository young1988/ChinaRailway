#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 Overpass 抓回的分段 way 处理成可直接渲染的线路数据。

处理流程:
  1. 按 name 筛选目标线路（同一 name 才是同一条线）
  2. 把成百上千个小 way 按端点拼接成连续 LineString
  3. Douglas-Peucker 简化
  4. WGS84 -> GCJ-02（腾讯/高德底图坐标系）
  5. 输出 lines_real.json

用法:
    python3 process_lines.py
"""
import json
import math
import os
import re
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(HERE, "raw")
OUT_JSON = os.path.join(os.path.dirname(HERE), "lines_real.json")

# 目标线路：显示名 -> (所属省份, OSM 候选线路名，按优先级取第一个命中的)
WANTED = {
    "京沪高铁": (["北京", "天津", "河北", "山东", "江苏", "上海"],
              ["京沪高铁", "京沪高速线", "京沪高速铁路"]),
    "京广高铁": (["北京", "河北", "河南", "湖北", "湖南", "广东"],
              ["京广高速线", "京广高铁", "京广线"]),
    "沪昆高铁": (["上海", "浙江", "江西", "湖南", "贵州", "云南"],
              ["沪昆高速线", "沪昆高铁", "沪昆线"]),
    # Demo 里京哈的经停站是普速线上的（唐山/秦皇岛/锦州南/沈阳北），
    # 优先用 京哈线；高铁本线（京哈高速线）走向不同，会让站点吸附跑偏上百公里
    "京哈线": (["北京", "河北", "辽宁", "吉林", "黑龙江"],
             ["京哈线", "京哈高速线"]),
    "陇海线": (["江苏", "安徽", "河南", "陕西", "甘肃"],
             ["陇海线"]),
    "京九线": (["北京", "河北", "山东", "河南", "安徽", "江西", "广东"],
             ["京九线"]),
    "兰新高铁": (["甘肃", "青海", "新疆"],
              ["兰新高铁", "兰新高速线", "兰新线"]),
    "成昆线": (["四川", "云南"],
             ["成昆线", "成昆铁路"]),
    "杭深线": (["浙江", "福建", "广东"],
             ["杭深线", "杭深高铁", "沿海铁路"]),
    "宁蓉线": (["江苏", "安徽", "湖北", "重庆", "四川"],
             ["宁蓉线", "沪蓉线", "沪汉蓉快速客运通道"]),
}

SIMPLIFY_TOL = float(os.environ.get("SIMPLIFY_TOL", 0.0015))  # 度，约 150m
# 碎 .chain 端点合并容差；太小各段接不上，太大会跨并行线造假边
MERGE_TOL = float(os.environ.get("MERGE_TOL", 0.012))    # 度，约 1.2km
# 保留链的最短点数。桥梁/隧道常是十几米到几百米的小 way，
# 阈值一高就会把它们整段丢掉，线路在桥隧处断开。
MIN_CHAIN = int(os.environ.get("MIN_CHAIN", 8))
COMBINE_ALL = os.environ.get("COMBINE_ALL", "1") == "1"

#  feeder 线路：OSM 里改了名字、但在运营上属于同一条通道的路段。
#  典型场景是终点站前的引线段（京九南下进深圳那一段在 OSM 叫「广深线」）。
#  这些名字要先用 fetch_lines.py <关键字> 抓下来。
FEEDERS = {
    "京九线": ["广深线"],
}

# ---------- 坐标系转换 WGS84 -> GCJ-02 ----------
_X_PI = 3.14159265358979324 * 3000.0 / 180.0
_A = 6378245.0
_EE = 0.00669342162296594323


def _transform_lat(x, y):
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lon(x, y):
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lat, lng):
    d_lat = _transform_lat(lng - 105.0, lat - 35.0)
    d_lng = _transform_lon(lng - 105.0, lat - 35.0)
    rad_lat = lat / 180.0 * math.pi
    magic = math.sin(rad_lat)
    magic = 1 - _EE * magic * magic
    sqrt_magic = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrt_magic) * math.pi)
    d_lng = (d_lng * 180.0) / (_A / sqrt_magic * math.cos(rad_lat) * math.pi)
    return lat + d_lat, lng + d_lng


# ---------- Douglas-Peucker ----------
def simplify(points, tol):
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        ax, ay = points[i]
        bx, by = points[j]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy)
        max_d, max_k = -1.0, -1
        for k in range(i + 1, j):
            px, py = points[k]
            if norm == 0:
                d = math.hypot(px - ax, py - ay)
            else:
                d = abs(dy * px - dx * py + bx * ay - by * ax) / norm
            if d > max_d:
                max_d, max_k = d, k
        if max_d > tol:
            keep[max_k] = True
            stack.append((i, max_k))
            stack.append((max_k, j))
    return [p for p, k in zip(points, keep) if k]


# ---------- 拼接：把一堆小段合成尽量长的连续链 ----------
def stitch(ways):
    key = lambda p: (round(p[0], 6), round(p[1], 6))
    segs = [[key(p) for p in w] for w in ways]
    # 端点 -> 段索引，出现 2 次以上说明是分歧点
    endpoint = defaultdict(list)
    for idx, s in enumerate(segs):
        endpoint[s[0]].append(idx)
        endpoint[s[-1]].append(idx)

    used = [False] * len(segs)
    chains = []

    def extend(chain, forward):
        while True:
            end = chain[-1] if forward else chain[0]
            nxt = None
            for i in endpoint.get(end, []):
                if used[i]:
                    continue
                s = segs[i]
                if s[0] == end or s[-1] == end:
                    nxt = i
                    break
            if nxt is None:
                return
            used[nxt] = True
            s = segs[nxt]
            piece = s[1:] if s[0] == end else s[:-1][::-1]
            if forward:
                chain.extend(piece)
            else:
                chain[:0] = piece

    order = sorted(range(len(segs)), key=lambda i: -len(segs[i]))
    for i in order:
        if used[i]:
            continue
        used[i] = True
        chain = list(segs[i])
        extend(chain, True)
        extend(chain, False)
        chains.append(chain)

    chains.sort(key=len, reverse=True)
    return chains


def merge_chains(chains, tol=0.0012):
    """把 stitch 后残留的碎 piec 按端点几乎重合(<tol，约 120m) 重新串起来。

    容差不能大，否则会把上下行并行正线首尾相接，形成假连接。
    """
    def gap(a, b):
        return math.hypot(a[0] - b[0], a[1] - b[1])

    cs = sorted((list(c) for c in chains), key=len, reverse=True)
    used = [False] * len(cs)
    out = []
    for i in range(len(cs)):
        if used[i]:
            continue
        used[i] = True
        cur = cs[i]
        changed = True
        while changed:
            changed = False
            for j in range(len(cs)):
                if used[j]:
                    continue
                c = cs[j]
                if gap(cur[-1], c[0]) < tol:
                    cur = cur + c[1:]
                elif gap(cur[-1], c[-1]) < tol:
                    cur = cur + c[::-1][1:]
                elif gap(cur[0], c[-1]) < tol:
                    cur = c[:-1] + cur
                elif gap(cur[0], c[0]) < tol:
                    cur = c[::-1][:-1] + cur
                else:
                    continue
                used[j] = True
                changed = True
        out.append(cur)
    return sorted(out, key=len, reverse=True)


def haversine_m(a, b):
    R = 6371000.0
    d_lat = math.radians(b[0] - a[0])
    d_lon = math.radians(b[1] - a[1])
    la1, la2 = math.radians(a[0]), math.radians(b[0])
    h = math.sin(d_lat / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(d_lon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def match_names(by_name, candidates):
    """选出该线路要参与建图的 OSM 名字集合。

    只取主线路名（如「京广高速线」）会在车站处断开：站内那段引轨通常
    挂在独立命名的联络线上（如「京广高速京西联络线」），缺了它线路就会
    停在站外几公里处。所以规则是：
      1) 命中的主线路名
      2) 与主线路同前缀、且名字里带「联络」的引线（几十条量级，噪声可控）
    刻意不并入同名系的普速线（京广线），避免走廊内串线。
    """
    hits = [c for c in candidates if c in by_name]
    if not hits:
        return [], None, []
    primary = hits[0]
    prefix = primary[:2]
    if COMBINE_ALL:
        return list(hits), primary, [n for n in hits if n != primary]
    picked = [primary]
    for n in by_name:
        if n != primary and n.startswith(prefix) and "联络" in n:
            picked.append(n)
    return picked, primary, [n for n in picked if n != primary]


def feeder_names(by_name, disp):
    """返回该线路要额外并入的 feeder 线路名（在 OSM 里换了名字的同通道路段）。"""
    out = []
    for n in FEEDERS.get(disp, []):
        if n in by_name:
            out.append(n)
    return out


def classify(tags_of, ways):
    """按**里程加权**判定线路类型，返回 (kind, 明细)。

    按 way 条数投票会被大量站场短股道带偏，必须按里程加权。
    三档：
      highspeed   高铁  —— highspeed=yes 且 大部分区段 >=250km/h
      conventional 普速 —— 标准轨既有线
      narrow      窄轨/米轨 —— gauge 非 1435（含 '1000;1435' 多值写法）
    """
    hs_km = 0.0          # highspeed=yes 里程
    fast_km = 0.0        # maxspeed>=250 里程
    gauge_km = defaultdict(float)
    sp_bucket = defaultdict(float)
    total = 0.0
    for tags, pts in zip(tags_of, ways):
        km = sum(haversine_m(pts[i], pts[i + 1]) for i in range(len(pts) - 1)) / 1000.0
        if km <= 0:
            continue
        total += km
        # gauge 可能是 '1000;1435' 这种多值
        for g in str(tags.get("gauge", "1435")).split(";"):
            g = g.strip()
            if g:
                gauge_km[g] += km
        if tags.get("highspeed") == "yes":
            hs_km += km
        try:
            sp = int(str(tags.get("maxspeed", "0")).split()[0])
        except Exception:
            sp = 0
        sp_bucket[sp] += km
        # 250 会把宁蓉/沪蓉这类 200~250 的动车道排除掉，按 200 更贴近用户直觉
        if sp >= 200:
            fast_km += km
    if total <= 0:
        return "conventional", {}
    narrow = sum(v for k, v in gauge_km.items() if k != "1435")
    info = {
        "km_total": round(total, 1),
        "km_highspeed": round(hs_km, 1),
        "km_fast": round(fast_km, 1),
        "km_narrow": round(narrow, 1),
        "gauges": {k: round(v, 1) for k, v in gauge_km.items()},
        "main_gauge": max(gauge_km.items(), key=lambda x: x[1])[0] if gauge_km else "1435",
        "maxspeed_top": max(sp_bucket.items(), key=lambda x: x[1])[0] if sp_bucket else 0,
    }
    # 判据：先看轨距，再看高铁标签+限速双条件（缺一通常是提速改造的既有线）
    if narrow > total * 0.5:
        kind = "narrow"
    elif hs_km > total * 0.5 and fast_km > total * 0.35:
        kind = "highspeed"
    else:
        kind = "conventional"
    return kind, info


def main():
    if not os.path.isdir(RAW_DIR):
        print("没有 raw 目录，先运行 fetch_lines.py")
        return

    result = {}
    report = []

    for fname in sorted(os.listdir(RAW_DIR)):
        if not fname.endswith(".json") or fname in ("summary.json",):
            continue
        with open(os.path.join(RAW_DIR, fname), encoding="utf-8") as f:
            data = json.load(f)
        by_name = defaultdict(list)
        tags_of = defaultdict(list)   # 与 by_name[name] 同下标，用于里程加权判类型
        for e in data.get("elements", []):
            if e.get("type") != "way":
                continue
            tags = e.get("tags", {})
            name = tags.get("name", "")
            geom = e.get("geometry", [])
            if name and geom:
                pts = [(p["lat"], p["lon"]) for p in geom]
                by_name[name].append(pts)
                tags_of[name].append(tags)

        for disp, (provs, candidates) in WANTED.items():
            picked, osm_name, links = match_names(by_name, candidates)
            feeders = feeder_names(by_name, disp)
            picked = picked + feeders
            if not picked:
                continue
            ways = [w for n in picked for w in by_name[n]]
            tags_sel = [t for n in picked for t in tags_of[n]]
            # 分类只统计主命中的那条线（osm_name）。
            # COMBINE_ALL 会把同走廊的普速线（「京广线」）也并进来帮忙接通 Vollständigkeit，
            # 但拿它们的里程当分母会把高铁占比稀释掉 —— 京广高铁就曾被判成普速。
            if osm_name and osm_name in by_name:
                kind, kinfo = classify(tags_of.get(osm_name, []), by_name[osm_name])
            else:
                kind, kinfo = classify(tags_sel, ways)
            chains = stitch(ways)
            # 丢弃过短的碎片段，保留主要连续段
            merged = merge_chains([c for c in chains if len(c) >= 3], MERGE_TOL)
            main_chains = [c for c in merged if len(c) >= MIN_CHAIN]
            total_raw = sum(len(c) for c in chains)
            paths = []
            longest_km = 0.0
            for c in main_chains:
                s = simplify(c, SIMPLIFY_TOL)
                gcj = [wgs84_to_gcj02(p[0], p[1]) for p in s]
                coords = [[round(a, 5), round(b, 5)] for a, b in gcj]
                km = sum(haversine_m(coords[i], coords[i + 1]) for i in range(len(coords) - 1)) / 1000.0
                longest_km = max(longest_km, km)
                paths.append(coords)
            result[disp] = {
                "osm_name": osm_name,
                "links": links,
                "provinces": provs,
                "kind": kind,
                "kindInfo": kinfo,
                "polylines": paths,
                # OSM 里上下行正线分开画，取最长一条作为线路里程参考
                "lengthKm": round(longest_km),
            }
            _H = kinfo.get("km_highspeed", 0)
            _N = kinfo.get("km_narrow", 0)
            report.append(
                f"{disp:<8} {kind:<12} "
                f"原始点 {total_raw:>6}  主链 {len(main_chains):>3} 段  "
                f"引/联线 {len(links) + len(feeders):>2} 条  简化后 {sum(len(p) for p in paths):>5} 点  "
                f"主线 {round(longest_km):>5} km  高铁段 {round(_H):>5} km  窄轨 {round(_N):>3} km"
            )

    print("\n".join(report))
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))
    size = os.path.getsize(OUT_JSON) / 1024
    # 同时输出可 <script> 直引的版本，避免 file:// 下 fetch 的跨域限制
    js_path = os.path.join(os.path.dirname(OUT_JSON), "lines_real.js")
    with open(js_path, "w", encoding="utf-8") as f:
        f.write("window.LINES_REAL=")
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")
    print(f"\n输出 {OUT_JSON} ({size:.0f} KB) 与 {js_path}  ({len(result)} 条线路)")


if __name__ == "__main__":
    main()
