#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""云南整省查询多次超时/被限流，拆成小块补抓后合并。"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_stations_all as F

BLOCKS = {
    "YN_NE": (24.0, 100.0, 29.3, 106.2),
    "YN_SW": (21.1, 97.5, 24.0, 103.5),
    "YN_SE": (21.1, 103.5, 24.0, 106.2),
}

elements = []
seen = set()
for nm, (s, w, n, e) in BLOCKS.items():
    for attempt in range(4):
        d = F.fetch(F.QUERY.format(s=s, w=w, n=n, e=e))
        if d:
            added = 0
            for x in d.get("elements", []):
                if x.get("type") != "node":
                    continue
                tg = x.get("tags", {})
                if not tg.get("name") or F.is_urban(tg):
                    continue
                if x.get("id") in seen:
                    continue
                seen.add(x.get("id"))
                elements.append(x)
                added += 1
            print("%s +%d (累计 %d)" % (nm, added, len(elements)), flush=True)
            break
        print("%s 第%d次失败" % (nm, attempt + 1), flush=True)
        time.sleep(12)
    time.sleep(3)

if elements:
    old = []
    p = os.path.join(F.OUT_DIR, "云南.json")
    if os.path.exists(p):
        try:
            old = json.load(open(p, encoding="utf-8")).get("elements", [])
        except Exception:
            old = []
    ids = {x.get("id") for x in elements}
    old = [x for x in old if x.get("id") not in ids]
    json.dump({"elements": old + elements}, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    print("云南完成: %d 条（含原有 %d）" % (len(old) + len(elements), len(old)))
else:
    print("云南仍未抓到")
