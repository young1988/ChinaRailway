#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取 Demo 中用到的车站真实坐标（来自 OSM railway=station/halt）。

Demo 里手写的站点坐标误差可达 8~15km，用它去匹配线路几何会吸附到错误的轨道上，
必须换成 OSM 的真实节点坐标。

输出:
    ../stations_real.js   window.STATIONS_REAL = { "北京南": [lat, lng], ... }  (GCJ-02)
"""
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BBOX = "18,73,54,136"

MIRRORS = [
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]


def fetch(query, timeout=120, tries=4):
    data = urllib.parse.urlencode({"data": query}).encode()
    for i in range(tries):
        for url in MIRRORS:
            try:
                req = urllib.request.Request(url, data=data, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    body = r.read().decode("utf-8")
                if body.strip().startswith("{"):
                    return json.loads(body)
            except Exception as e:
                print(f"    {type(e).__name__}: {e}")
            time.sleep(2)
        print(f"    第 {i+1} 轮失败，重试")
        time.sleep(6)
    return None


def read_station_names():
    """返回 [(站名, lat, lng)]，保留手写坐标用于同名节点的消歧。"""
    html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    pairs = re.findall(r"\['([^']{2,8})',\s*(\d+\.\d+),\s*(\d+\.\d+)\]", html)
    seen, out = set(), []
    for n, la, lo in pairs:
        if n not in seen:
            seen.add(n)
            out.append((n, float(la), float(lo)))
    return out


def to_gcj(lat, lng):
    """WGS84 -> GCJ-02"""
    a, ee = 6378245.0, 0.00669342162296594323

    def t_lat(x, y):
        r = -100.0 + 2.0*x + 3.0*y + 0.2*y*y + 0.1*x*y + 0.2*math.sqrt(abs(x))
        r += (20.0*math.sin(6.0*x*math.pi) + 20.0*math.sin(2.0*x*math.pi)) * 2.0/3.0
        r += (20.0*math.sin(y*math.pi) + 40.0*math.sin(y/3.0*math.pi)) * 2.0/3.0
        r += (160.0*math.sin(y/12.0*math.pi) + 320*math.sin(y*math.pi/30.0)) * 2.0/3.0
        return r

    def t_lon(x, y):
        r = 300.0 + x + 2.0*y + 0.1*x*x + 0.1*x*y + 0.1*math.sqrt(abs(x))
        r += (20.0*math.sin(6.0*x*math.pi) + 20.0*math.sin(2.0*x*math.pi)) * 2.0/3.0
        r += (20.0*math.sin(x*math.pi) + 40.0*math.sin(x/3.0*math.pi)) * 2.0/3.0
        r += (150.0*math.sin(x/12.0*math.pi) + 300.0*math.sin(x/30.0*math.pi)) * 2.0/3.0
        return r

    d_lat = t_lat(lng - 105.0, lat - 35.0)
    d_lon = t_lon(lng - 105.0, lat - 35.0)
    rad = lat / 180.0 * math.pi
    magic = 1 - ee * math.sin(rad) ** 2
    sq = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / ((a * (1 - ee)) / (magic * sq) * math.pi)
    d_lon = (d_lon * 180.0) / (a / sq * math.cos(rad) * math.pi)
    return round(lat + d_lat, 6), round(lng + d_lon, 6)


def main():
    stations = read_station_names()
    names = [s[0] for s in stations]
    hand = {s[0]: (s[1], s[2]) for s in stations}
    print(f"待查车站 {len(names)} 个")

    # 中国境内同名站点很多，收集候选后按 (名称匹配度, 与手写坐标距离) 打分择优
    cands = {n: [] for n in names}
    batch = 30
    for i in range(0, len(names), batch):
        part = names[i:i + batch]
        pat = "|".join(re.escape(n) for n in part)
        query = (
            f'[out:json][timeout:120][bbox:{BBOX}];'
            f'node["railway"~"^(station|halt)$"]["name"~"{pat}"];'
            'out body;'
        )
        data = fetch(query)
        if not data:
            continue
        for e in data.get("elements", []):
            tags = e.get("tags", {})
            if tags.get("railway") not in ("station", "halt"):
                continue
            lat, lng = e.get("lat"), e.get("lon")
            if lat is None:
                continue
            nm = tags.get("name", "")
            key = nm[:-1] if nm.endswith("站") and len(nm) > 2 else nm
            if key not in cands:
                continue
            name_score = 2 if nm in (key, key + "站") else 0
            name_score += 1 if tags.get("railway") == "station" else 0
            hl, hg = hand[key]
            near = math.hypot(lat - hl, lng - hg)   # 与手写坐标的距离（度）
            if near > 0.5:                          # 超过约 50km 视为同名异地节点
                continue
            cands[key].append((name_score, -near, lat, lng))

    result = {}
    for n in names:
        if not cands[n]:
            continue
        cands[n].sort(reverse=True)
        _, _, lat, lng = cands[n][0]
        result[n] = to_gcj(lat, lng)

    miss = [n for n in names if n not in result]
    print(f"\n匹配成功 {len(result)}/{len(names)}，缺 {len(miss)}")
    if miss:
        print("  未匹配:", "、".join(miss))

    out = os.path.join(ROOT, "stations_real.js")
    with open(out, "w", encoding="utf-8") as f:
        f.write("window.STATIONS_REAL=")
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")
    print(f"输出 {out}")


if __name__ == "__main__":
    main()
