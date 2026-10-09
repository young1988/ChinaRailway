#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补抓 fetch_conventional.py 里失败的省块，合并回原文件。

分块顺序与 split_bbox 一致：[西北, 东北, 西南, 东南]（1-based 即 1,2,3,4）。
用法:
    python3 patch_conv.py 山东:3 重庆:4
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_conventional as F


def patch(prov, block_no):
    path = os.path.join(F.OUT_DIR, f"{prov}.json")
    if not os.path.exists(path):
        print(f"{prov}: 原始文件不存在，请先整省抓取")
        return
    blocks = F.split_bbox(F.PROVINCES[prov])
    if block_no > len(blocks):
        print(f"{prov}: 只有 {len(blocks)} 块，不存在第 {block_no} 块")
        return
    blk = blocks[block_no - 1]
    print(f"{prov} 第 {block_no}/{len(blocks)} 块 bbox={blk}", flush=True)

    data = F.fetch(F.build_query(blk))
    if not data:
        print(f"  ✗ 仍然失败")
        return

    old = json.load(open(path, encoding="utf-8"))
    seen = {e.get("id") for e in old.get("elements", []) if e.get("type") == "way"}
    added = 0
    for e in data.get("elements", []):
        if e.get("type") == "way" and e.get("id") not in seen:
            seen.add(e.get("id"))
            old.setdefault("elements", []).append(e)
            added += 1
    with open(path, "w", encoding="utf-8") as f:
        json.dump(old, f, ensure_ascii=False)
    print(f"  ✓ 补入 {added} 条新 way，该文件现有 {len(seen)} 条")


if __name__ == "__main__":
    args = sys.argv[1:] or ["山东:3", "重庆:4"]
    for a in args:
        prov, no = a.split(":")
        patch(prov, int(no))
        time.sleep(3)
