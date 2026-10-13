#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成 railway-demo/line_alias.js：线路别名层（别名 → 标准线路名）。

背景：OSM 里一条铁路的 `name` 与它的中文维基名经常不同。典型如「洛湛铁路」——
它在 OSM 里的 name 是「益湛线」，「洛湛」只写在 wikipedia:zh:洛湛铁路 标签里。
线路图鉴的搜索只匹配 name，于是用户搜「洛湛」什么都搜不到，误以为铁路丢了。
本脚本把 wikipedia:zh:* 提成别名，让搜索能按常用名/旧名/通道名命中。

数据源：tools/raw_conv/*.json、tools/raw_hs/*.json 的 wikipedia 标签
产物：railway-demo/line_alias.js（window.RW_LINE_ALIAS = {别名: [标准名...]}）

两类别名：
  1. 自动提取：wikipedia:zh:X 与 name 去掉「铁路/线」后缀后不同，且 X 不是
     桥梁/隧道/工程计划名（那些是「线上的构造物」，不是线的别名）
  2. 手工补正：通道名/工程名，OSM 拆成多条既有线，需要一对多映射
     （如 洛湛铁路 → 焦柳线 + 益湛线 + 马玉线）

用法：python3 tools/build_line_alias.py
"""
import glob
import json
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# 这些词说明 wiki 名指的是「线上的构造物/工程」，不是线路本身的别名
BAD_WORDS = ('橋', '桥', '隧道', '計畫', '计划', '地下化', '特大')

# 手工补正：通道名 / 工程名 → 组成它的既有线路（OSM 按既有线命名，没有这个 name）
MANUAL = {
    '洛湛铁路': ['焦柳线', '益湛线', '马玉线'],
    '洛湛线': ['焦柳线', '益湛线', '马玉线'],
    # 益湛线的 loc_name 就是「永玉铁路」（永州—玉林段），其独立 name 段太短被 MIN_KM 丢弃
    '永玉铁路': ['益湛线'],
}

STRIP_SUFFIX = ('铁路', '高铁', '线')


def norm(s):
    """去掉括号备注与线路后缀，便于比较。"""
    s = re.sub(r'\([^)]*\)', '', s or '')
    s = s.strip()
    for suf in STRIP_SUFFIX:
        if s.endswith(suf) and len(s) > len(suf):
            s = s[:-len(suf)]
            break
    return s.strip()


def load_names():
    """现有线路名（别名必须指向真实存在的线路，否则是死链）。"""
    names = set()
    for f in ('highspeed_lines.js', 'conventional_lines.js'):
        p = os.path.join(ROOT, f)
        if not os.path.exists(p):
            continue
        txt = open(p, encoding='utf-8').read()
        m = re.search(r'=\s*(\{[\s\S]*\})\s*;?\s*$', txt)
        if m:
            names |= set(json.loads(m.group(1))['lines'].keys())
    return names


def main():
    known = load_names()
    # 别名 -> 标准名集合
    alias = defaultdict(set)

    def add(a, std):
        if a and std in known and a != std:
            alias[a].add(std)

    files = sorted(glob.glob(os.path.join(HERE, 'raw_conv', '*.json'))) + \
            sorted(glob.glob(os.path.join(HERE, 'raw_hs', '*.json')))
    seen = set()
    n_wp = 0
    for path in files:
        try:
            data = json.load(open(path, encoding='utf-8'))
        except Exception:
            continue
        for e in data.get('elements', []):
            if e.get('type') != 'way':
                continue
            wid = e.get('id')
            if wid in seen:
                continue
            tags = e.get('tags', {}) or {}
            name, wp = tags.get('name'), tags.get('wikipedia') or ''
            if not name or not wp.startswith('zh:'):
                continue
            seen.add(wid)
            n_wp += 1
            wpn = wp[3:].strip()
            if any(b in wpn for b in BAD_WORDS):
                continue                       # 桥梁/隧道/工程计划，不是线路别名
            if norm(wpn) == norm(name):
                continue                       # 只是写法差异（如带「铁路」后缀）
            add(wpn, name)                     # 别名保留原始写法，如「洛湛铁路」

    # 手工补正（通道名一对多）
    for a, stds in MANUAL.items():
        for s in stds:
            add(a, s)

    out = {a: sorted(v) for a, v in sorted(alias.items())}
    dead = [a for a, v in out.items() if not v]
    for a in dead:
        del out[a]

    path = os.path.join(ROOT, 'line_alias.js')
    lines = [
        '/* 线路别名层：别名 -> 标准线路名（供线路图鉴按常用名/通道名搜索）',
        '   数据源：OSM 的 wikipedia:zh:* 标签（name 与维基名经常不同，',
        '   如「洛湛铁路」在 OSM 里的 name 是「益湛线」）+ 少量通道名手工补正',
        '   生成：python3 tools/build_line_alias.py',
        '   注意：桥梁/隧道/工程计划名已剔除——那些是「线上的构造物」，不是线的别名 */',
        'window.RW_LINE_ALIAS = ' + json.dumps(out, ensure_ascii=False, separators=(',', ':')) + ';',
        '',
    ]
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print('扫描 way %d 个（带 wikipedia:zh 标签）' % n_wp)
    print('生成别名 %d 条 -> %s（%d KB）' % (len(out), path, os.path.getsize(path) // 1024 or 1))
    if dead:
        print('丢弃 %d 条（标准名不存在于线路数据）' % len(dead))
    # 抽样展示
    for a in ('洛湛铁路', '洛湛线', '永玉铁路', '成兰', '昆楚大', '深湛', '南昆客运专'):
        if a in out:
            print('  %-10s -> %s' % (a, '/'.join(out[a])))


if __name__ == '__main__':
    main()
