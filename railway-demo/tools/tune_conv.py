#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对同一批 raw_conv 数据跑多组参数，比较输出体积与点数，用于标定压缩参数。

用法:
    python3 tune_conv.py
"""
import os
import subprocess
import sys
import json
import itertools

PY = sys.executable
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "conventional_lines.js")

GRID = [
    {"MIN_KM": 40, "SIMPLIFY_TOL": 0.003, "NDIGITS": 4},
    {"MIN_KM": 50, "SIMPLIFY_TOL": 0.004, "NDIGITS": 4},
    {"MIN_KM": 60, "SIMPLIFY_TOL": 0.005, "NDIGITS": 4},
    {"MIN_KM": 60, "SIMPLIFY_TOL": 0.006, "NDIGITS": 4},
    {"MIN_KM": 80, "SIMPLIFY_TOL": 0.005, "NDIGITS": 4},
]


def run(params):
    env = dict(os.environ)
    env.update({k: str(v) for k, v in params.items()})
    # 捕获脚本打印的最后一行（含点数与文件体积）
    p = subprocess.run([PY, "-u", os.path.join(HERE, "process_conventional.py")],
                       env=env, capture_output=True, text=True, cwd=HERE)
    tail = [l for l in p.stdout.strip().split("\n") if "条线路," in l]
    if not tail:
        return None
    return tail[-1]


if __name__ == "__main__":
    print(f"{'MIN_KM':>7}{'TOL':>8}{'DIG':>5} | {'线路':>6}{'点数':>8}{'文件KB':>8}")
    print("-" * 52)
    best = None
    for p in GRID:
        line = run(p)
        if not line:
            print(f"{p['MIN_KM']:>7}{p['SIMPLIFY_TOL']:>8}{p['NDIGITS']:>5} | 运行失败")
            continue
        # 形如 "  298 条线路, 29054 点, 主线累计 66469 km, 文件 575 KB"
        nums = [int(s) for s in line.replace("条线路", " ").replace("点", " ")
                .replace("主线累计", " ").replace("km", " ").replace("文件", " ")
                .replace("KB", " ").replace(",", " ").split() if s.isdigit()]
        if len(nums) < 5:
            print(f"{p['MIN_KM']:>7}{p['SIMPLIFY_TOL']:>8}{p['NDIGITS']:>5} | 解析失败: {line}")
            continue
        cnt, pts, _km, kb = nums[0], nums[1], nums[2], nums[3]
        print(f"{p['MIN_KM']:>7}{p['SIMPLIFY_TOL']:>8}{p['NDIGITS']:>5} | "
              f"{cnt:>6}{pts:>8}{kb:>8}")
    print("\n注：每次都会覆写 conventional_lines.js，最后再跑一次选定参数收尾。")
