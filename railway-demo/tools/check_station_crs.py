#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""比对 raw_st 的 OSM 原始坐标与 stations_all.js 存的坐标，验证是否做过 GCJ-02 转换。"""
import glob
import json
import os
import random
import re
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)

txt = open(os.path.join(ROOT, 'stations_all.js'), encoding='utf-8').read()
body = re.search(r'STATIONS_ALL="([\s\S]*)"\s*;?\s*$', txt).group(1)
stored = {}
for line in body.split('\\n'):
    if not line.strip():
        continue
    p = line.split('|')
    if len(p) >= 4:
        stored[p[0]] = (p[1], float(p[2]), float(p[3]))

raw = {}
for f in glob.glob(os.path.join(TOOLS, 'raw_st', '*.json')):
    try:
        d = json.load(open(f, encoding='utf-8'))
    except Exception:
        continue
    for e in d.get('elements', []):
        if e.get('type') != 'node':
            continue
        t = e.get('tags', {}) or {}
        if t.get('railway') in ('station', 'halt') and t.get('name'):
            raw[str(e['id'])] = (t['name'], e['lat'], e['lon'])

# 引入转换函数算偏差
sys.path.insert(0, TOOLS)
from process_lines import wgs84_to_gcj02

random.seed(7)
sample = random.sample(list(stored.keys()), 12)
print('%-12s %-8s %-21s %-21s %-9s %s' % ('id', '站名', '库里存的', '应为 GCJ-02', '偏差', '判定'))
ok = 0
for i in sample:
    s_ = stored[i]
    r = raw.get(i)
    if not r:
        print('%-12s %-8s (raw 中无此 id)' % (i, s_[0]))
        continue
    gla, glo = wgs84_to_gcj02(r[1], r[2])
    # 用 haversine 估偏差
    import math
    R = 6371000.0
    p1, p2 = math.radians(s_[1]), math.radians(gla)
    h = math.sin((p1 - p2) / 2) ** 2 + math.cos(p1) * math.cos(p2) \
        * math.sin(math.radians(s_[2] - glo) / 2) ** 2
    dev = 2 * R * math.asin(math.sqrt(h))
    good = dev < 20
    ok += 1 if good else 0
    print('%-12s %-8s %.5f,%.5f      %.5f,%.5f      %6.0f m  %s'
          % (i, s_[0], s_[1], s_[2], gla, glo, dev, 'OK' if good else '未转换(仍是 WGS-84)'))
print('\n抽样 %d 个中，%d 个已正确转换为 GCJ-02（偏差 < 20 m）' % (len(sample), ok))
if ok < len(sample):
    print('→ 车站坐标与线路几何坐标系不一致：腾讯地图按 GCJ-02 渲染，'
          '未转换会让所有站点标记系统性偏离真实站位 480~620 m。')
    sys.exit(1)
