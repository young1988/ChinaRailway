#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""广东一次性查询会被 Overpass 限流/网关超时，拆成 4 块分别请求后合并。"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fetch_highspeed as F  # noqa: E402

BLOCKS = {
    "GD_珠三角": (21.8, 112.4, 23.9, 115.0),
    "GD_粤东":   (22.8, 113.5, 24.9, 117.3),
    "GD_粤西":   (20.2, 109.4, 23.5, 112.5),
    "GD_粤北":   (23.6, 111.5, 25.6, 116.5),
}


def main():
    q_tpl = ('[out:json][timeout:180][bbox:%s,%s,%s,%s];'
             'way["railway"="rail"]["usage"="main"]["highspeed"="yes"];'
             'out tags geom;')
    elements, seen = [], set()
    for nm, bbox in BLOCKS.items():
        print(f"--- {nm} ---", flush=True)
        for attempt in range(4):
            data = F.fetch(q_tpl % bbox)
            if data:
                n = 0
                for e in data.get("elements", []):
                    wid = e.get("id")
                    if e.get("type") == "way" and wid not in seen:
                        seen.add(wid)
                        elements.append(e)
                        n += 1
                print(f"  +{n} 条（累计 {len(elements)}）", flush=True)
                break
            print(f"  第 {attempt + 1} 次失败，等 20s", flush=True)
            time.sleep(20)
        time.sleep(5)
    out = os.path.join(HERE, "raw_hs", "广东.json")
    json.dump({"elements": elements}, open(out, "w", encoding="utf-8"),
              ensure_ascii=False)
    print(f"✅ 广东完成：{len(elements)} 条 way -> {out}", flush=True)


if __name__ == "__main__":
    main()
