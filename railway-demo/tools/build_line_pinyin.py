#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 highspeed_lines.js / conventional_lines.js 提取全部线路名中出现过的汉字，
用 GB2312 一级汉字分区表推算拼音首字母，生成 line_py.js（离线、无需联网/第三方库）。

字符来源有两处，缺一不可：
  1. 592 条线路名（主来源）
  2. line_alias.js 的别名（如「洛湛铁路」）——别名里的字不会出现在任何线路名里
     （没有任何线路名叫「洛…」），漏掉它别名的首字母搜索就会失效

产物：railway-demo/line_py.js
    window.RW_LINE_PY = { "京":"J", "沪":"H", ... };
供 index.html 的线路图鉴做「首字母搜索」与「按字母分组」。

GB2312 一级汉字（0xB0A1–0xD7F9）按拼音排序，各首字母起始码位如下。
ASCII 字母/数字原样保留；非 GB2312 汉字（生僻字）回退为 '#'。

用法（先生成别名层，再生成拼音表，顺序不能反）：
    python3 tools/build_line_alias.py && python3 tools/build_line_pinyin.py
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# 首字母 -> GB2312 起始码位（标准分区表，末段 0xD7FA 为收尾哨兵）
BOUNDS = [
    (0xB0A1, 'A'), (0xB0C5, 'B'), (0xB2C1, 'C'), (0xB4EE, 'D'),
    (0xB6EA, 'E'), (0xB7A2, 'F'), (0xB8C1, 'G'), (0xB9FE, 'H'),
    (0xBBF7, 'J'), (0xBFA6, 'K'), (0xC0AC, 'L'), (0xC2E8, 'M'),
    (0xC4C3, 'N'), (0xC5B6, 'O'), (0xC5BE, 'P'), (0xC6DA, 'Q'),
    (0xC8BB, 'R'), (0xC8F6, 'S'), (0xCBFA, 'T'), (0xCDDA, 'W'),
    (0xCEF4, 'X'), (0xD1B9, 'Y'), (0xD4D1, 'Z'), (0xD7FA, '^'),
]


# GB2312 之外的生僻字与繁体字（GB2312 分区表覆盖不到），人工补正
OVERRIDE = {
    '亳': 'B', '兖': 'Y', '卦': 'G', '妃': 'F', '岚': 'L', '岢': 'K', '梓': 'Z',
    '泸': 'L', '滠': 'S', '漯': 'L', '珞': 'L', '珲': 'H', '甬': 'Y', '畲': 'S',
    '邕': 'Y', '郫': 'P', '醴': 'L', '闵': 'M', '衢': 'Q', '艮': 'G',
    # 繁体/异体（OSM 线路名与别名里偶有）
    '東': 'D', '馬': 'M', '鐵': 'T', '臺': 'T', '灣': 'W', '蘭': 'L',
    '線': 'X', '綫': 'X', '縱': 'Z', '貫': 'G', '迴': 'H',
    # 繁体别名用字（line_alias.js：懷衡鐵路 / 哈羅鐵路 / 成貴客運專線 …）
    '專': 'Z', '懷': 'H', '羅': 'L', '貴': 'G', '運': 'Y',
}


def initial(ch: str) -> str:
    """单字 -> 拼音首字母（大写）。"""
    if ch in OVERRIDE:
        return OVERRIDE[ch]
    if 'a' <= ch <= 'z' or 'A' <= ch <= 'Z':
        return ch.upper()
    if '0' <= ch <= '9':
        return '#'
    try:
        b = ch.encode('gb2312')
    except UnicodeEncodeError:
        return '#'
    if len(b) != 2:
        return '#'
    code = (b[0] << 8) | b[1]
    if code < BOUNDS[0][0] or code >= BOUNDS[-1][0]:
        return '#'
    for i in range(len(BOUNDS) - 1):
        if BOUNDS[i][0] <= code < BOUNDS[i + 1][0]:
            return BOUNDS[i][1]
    return '#'


def load_lines(path: str) -> dict:
    with open(path, 'r', encoding='utf-8') as f:
        s = f.read()
    m = re.search(r'=\s*(\{[\s\S]*\})\s*;?\s*$', s)
    return json.loads(m.group(1))['lines']


def load_alias_names():
    """line_alias.js 的别名也要进表：别名里的字（如「洛」）不出现在任何线路名里。"""
    p = os.path.join(ROOT, 'line_alias.js')
    if not os.path.exists(p):
        return []
    m = re.search(r'window\.RW_LINE_ALIAS\s*=\s*(\{.*?\})\s*;', open(p, encoding='utf-8').read(), re.S)
    if not m:
        return []
    try:
        return list(json.loads(m.group(1)).keys())
    except Exception:
        return []


def main():
    names = []
    for f in ('highspeed_lines.js', 'conventional_lines.js'):
        names += list(load_lines(os.path.join(ROOT, f)).keys())
    aliases = load_alias_names()
    names += aliases

    chars = {}
    for n in names:
        for c in n:
            if c not in chars:
                chars[c] = initial(c)

    table = {c: v for c, v in sorted(chars.items())}
    fallback = sorted([c for c, v in table.items() if v == '#'])

    out = os.path.join(ROOT, 'line_py.js')
    lines = [
        '/* 线路名汉字 -> 拼音首字母（离线生成，供线路图鉴「首字母搜索 / 按字母分组」使用）',
        '   数据源：OSM 起名的 highspeed_lines.js / conventional_lines.js 全部线路名',
        '           + line_alias.js 的别名（别名里的字不在任何线路名中，必须一并收）',
        '   生成：python3 tools/build_line_alias.py && python3 tools/build_line_pinyin.py',
        '         （GB2312 一级汉字分区表，非完整拼音，多音字取常用音）',
        '   字母表：%d 字；回退 %d 字（生僻字记 #） */' % (len(table), len(fallback)),
        'window.RW_LINE_PY = ' + json.dumps(table, ensure_ascii=False, separators=(',', ':')) + ';',
        '',
    ]
    with open(out, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print('线路名 %d 条 + 别名 %d 条' % (len(names) - len(aliases), len(aliases)))
    print('字符表 %d 字 -> %s' % (len(table), out))
    print('回退(#) %d 字: %s' % (len(fallback), ''.join(fallback) if fallback else '无'))
    # 抽样自检
    for s in ('京九线', '沪昆高速线', '兰新客专线', '广深线', '杭深线', '敦煌铁路'):
        print('  %s -> %s' % (s, ''.join(table.get(c, initial(c)) for c in s)))


if __name__ == '__main__':
    main()
