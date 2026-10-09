#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 raw_st/ 里的 OSM 车站节点清洗成前端可用的站名→坐标表。

关键处理：
  1. 按 OSM id 去重（省 bbox 互相重叠，同一节点会被多个省抓到）
  2. 剔除境外车站：省 bbox 太粗会框进俄/蒙/朝/越/哈/印的车站
     —— 判据不是字符集（越南语是拉丁扩展区，靠字符集漏得厉害），
        而是「名字里必须含有中日韩汉字」，且坐标落在中国境内
  3. 名字归一化：取最长的汉字连续段，去掉后缀「站」，保留原名作别名
  4. 同名不同地的站（如多个「泰安」）全部保留，由前端按上下文消歧

输出（放在 railway-demo/ 根目录）：
  stations_all.json   调试用
  stations_all.js     window.STATIONS_ALL = "名字|纬度|经度[|别名]\n..."
"""
import glob
import json
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw_st")
ROOT = os.path.dirname(HERE)

# 中国境内大致范围（含中国台湾/中国香港/中国澳门）
LAT_MIN, LAT_MAX = 17.0, 54.5
LON_MIN, LON_MAX = 73.0, 135.5

CJK = re.compile(r"[\u3400-\u9fff]+")     # 含扩展 A 区，覆盖繁体
TRAIL_STATION = re.compile(r"站$")


def norm_name(raw):
    """取最长汉字段；返回 (主名, 别名或 None)"""
    runs = CJK.findall(raw)
    if not runs:
        return None, None
    base = max(runs, key=len)
    if len(base) < 2:
        return None, None
    name = TRAIL_STATION.sub("", base)
    if len(name) < 2:                      # 「站」这种被剥光的，退回原汉字串
        name = base
    alt = base if base != name else None
    return name, alt


def load():
    by_id = {}
    for f in sorted(glob.glob(os.path.join(RAW, "*.json"))):
        if f.endswith("summary.json"):
            continue
        try:
            data = json.load(open(f, encoding="utf-8"))
        except Exception as e:
            print("  跳过 %s (%s)" % (os.path.basename(f), e))
            continue
        for e in data.get("elements", []):
            if e.get("type") != "node":
                continue
            by_id[e.get("id")] = e
    return by_id


def main():
    nodes = load()
    print("去重后节点: %d" % len(nodes))

    foreign = 0
    noname = 0
    merged = 0
    seen = {}            # (name, lat1, lon1) -> True  约 11km 网格，用于合并重复站点
    pool = defaultdict(list)   # name -> [(lat, lon)]

    for e in nodes.values():
        tags = e.get("tags", {})
        raw = (tags.get("name") or "").strip()
        lat, lon = e.get("lat"), e.get("lon")
        if not raw or lat is None or lon is None:
            noname += 1
            continue
        if not (LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX):
            foreign += 1
            continue
        name, alt = norm_name(raw)
        if not name:
            foreign += 1                 # 名字里没有汉字 → 境外站
            continue
        key = (name, round(lat, 1), round(lon, 1))
        if key in seen:
            merged += 1
            continue
        seen[key] = True
        rec = pool[name]
        if not any(abs(a - lat) < 0.02 and abs(b - lon) < 0.02 for a, b in rec):
            rec.append((lat, lon))

    print("剔除境外/无汉字名: %d   无坐标: %d   合并重复: %d" % (foreign, noname, merged))
    print("最终站名数: %d   站点记录: %d"
          % (len(pool), sum(len(v) for v in pool.values())))

    # 输出：紧凑文本，运行时 split 解析
    rows = []
    for name in sorted(pool):
        for lat, lon in pool[name]:
            rows.append("%s|%.4f|%.4f" % (name, lat, lon))
    text = "\n".join(rows)

    json.dump(
        {
            "crs": "GCJ-02",
            "count": len(pool),
            "records": len(rows),
            "stations": [
                {"n": r.split("|")[0], "lat": float(r.split("|")[1]),
                 "lon": float(r.split("|")[2])}
                for r in rows
            ],
        },
        open(os.path.join(ROOT, "stations_all.json"), "w", encoding="utf-8"),
        ensure_ascii=False,
    )
    with open(os.path.join(ROOT, "stations_all.js"), "w", encoding="utf-8") as f:
        f.write("/* 全国铁路车站表（OSM railway=station/halt，已转 GCJ-02）\n"
                "   格式：名字|纬度|经度，按行分隔；同名不同地会有多行，由前端按上下文消歧 */\n")
        f.write("window.STATIONS_ALL=" + json.dumps(text, ensure_ascii=False) + ";\n")

    size = os.path.getsize(os.path.join(ROOT, "stations_all.js"))
    print("输出 stations_all.js  %.0f KB（%d 条记录）" % (size / 1024, len(rows)))


if __name__ == "__main__":
    main()
