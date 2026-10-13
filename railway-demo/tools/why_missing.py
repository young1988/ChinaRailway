#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断为什么某些 OSM 线路名没进 conventional_lines.js（如洛湛通道的岑茂铁路/永玉铁路）。"""
import sys, os, json, glob, collections
TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
import process_lines as P   # noqa: 复用 stitch / merge_chains / simplify / 坐标转换

RAW = os.path.join(TOOLS, 'raw_conv')

def collect(targets):
    out = collections.defaultdict(list)
    seen = set()
    for fn in sorted(glob.glob(os.path.join(RAW, '*.json'))):
        try:
            data = json.load(open(fn, encoding='utf-8'))
        except Exception:
            continue
        for e in data.get('elements', []):
            if e.get('type') != 'way':
                continue
            wid = e.get('id')
            if wid in seen:
                continue
            geom = e.get('geometry') or []
            tags = e.get('tags', {}) or {}
            name = tags.get('name')
            if not name or len(geom) < 2:
                continue
            if name in targets:
                seen.add(wid)
                out[name].append(([(p['lat'], p['lon']) for p in geom], tags))
    return out

def report(name, items, min_km=40, link_min_km=25, merge_tol=0.012):
    ways = [w for w, _ in items]
    chains = P.stitch(ways)
    merged = P.merge_chains([c for c in chains if len(c) >= 3], merge_tol)
    main = [c for c in merged if len(c) >= 5]   # 与 process_conventional 的 MIN_CHAIN 默认一致
    total = 0.0
    for c in main:
        simp = P.simplify(c, 0.005)
        total += sum(P.haversine_m(simp[i], simp[i + 1]) for i in range(len(simp) - 1)) / 1000.0
    kinds, kinfo = P.classify([t for _, t in items], ways)
    is_link = any('联络' in name or '疏解' in name or '引线' in name for _ in [0])
    thr = link_min_km if is_link else min_km
    verdict = '保留' if total >= thr else ('被 MIN_KM 丢弃' if not is_link else '被 LINK_MIN_KM 丢弃')
    print('%-12s ways=%-4d 链=%-3d 合并后=%-3d 总里程=%7.1f km  阈值=%d  → %s'
          % (name, len(items), len(chains), len(main), total, thr, verdict))

if __name__ == '__main__':
    targets = sys.argv[1:] or ['岑茂铁路', '永玉铁路', '益阳东疏解线', '马玉线', '益湛线', '焦柳线']
    got = collect(set(targets))
    for t in targets:
        if t in got:
            report(t, got[t])
        else:
            print('%-12s raw 中未找到（可能被上一级 skip 规则剔除）' % t)
