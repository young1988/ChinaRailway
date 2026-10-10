#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""线路连通质量诊断：把「断裂」分成真缺口与假断裂，用于调参前后对比。

为什么需要它
  「一线多段」不等于「线断了」。实测many 线路被算成断裂，真实原因有三类：
    1. 短引轨残片：主线完整，旁边挂一条1~6km 的站场引轨（胶济线 716km 主线 + 1.3km 引轨）
    2. 同名异地：OSM 里同名但地理上相隔数百公里的两段线路（川藏铁路 成都段 + 西藏段）
    3. 超大枢纽并行线：跨千公里干线被切成多段，最近端点差 1000km+ 属正常结构（京广/京港）
  这三类都不是「OSM 缺数据」，管理员手工补线是白忙。

真缺口定义（要管理员动手的）
  一线多段 + 存在长度可观的段(>MINSIG km) + 任意两段最近端点 >THRESH km
  且该缺口看起来是同一条走廊被截断（用共线性/走向一致性做二次确认）。

用法
  python3 tools/diagnose_gaps.py            # 人读报告
  python3 tools/diagnose_gaps.py --json     # 机器读，便于对比两轮
"""
import json
import math
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

THRESH = 8.0      # km，两段最近端点超过此值才算断
MINSIG = 15.0     # km，段长下限，低于此视为引轨残片
CORRIDOR = 0.86   # 走向一致性余弦阈值（>0.86 视为同一走廊，cos 60°=0.5）
SLIVER = 2.0      # km，判定缺口时忽略的「碎屑段」长度；与它配对不构成真缺口


def load(fn, var):
    txt = open(os.path.join(ROOT, fn), encoding="utf-8").read()
    return json.loads(txt.split("window." + var + "=", 1)[1].rstrip().rstrip(";"))


def hav(a, b):
    R = 6371.0
    p = math.pi / 180.0
    dlat = (b[0] - a[0]) * p
    dlon = (b[1] - a[1]) * p
    x = math.sin(dlat / 2) ** 2 + math.cos(a[0] * p) * math.cos(b[0] * p) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(min(1.0, x)))


def plen(c):
    return sum(hav(c[k], c[k + 1]) for k in range(len(c) - 1))


def endpoint(c, frac):
    """取折线从头(frac=0)或尾(frac=1)方向、沿长度 frac 处 的点与切向。"""
    total = plen(c)
    if total <= 0:
        return c[0], (0.0, 0.0)
    target = total * frac
    acc = 0.0
    for i in range(len(c) - 1):
        d = hav(c[i], c[i + 1])
        if acc + d >= target or i == len(c) - 2:
            t = 0.0 if d == 0 else min(1.0, (target - acc) / d)
            px = c[i][0] + (c[i + 1][0] - c[i][0]) * t
            py = c[i][1] + (c[i + 1][1] - c[i][1]) * t
            # 切向
            k = max(1, int(len(c) * 0.02))
            a = c[max(0, i - k)]
            b = c[min(len(c) - 1, i + 1 + k)]
            vx, vy = b[0] - a[0], b[1] - a[1]
            n = math.hypot(vx, vy) or 1.0
            return (px, py), (vx / n, vy / n)
        acc += d
    return c[-1], (0.0, 0.0)


def bearing(pt, vec):
    return math.atan2(vec[0], vec[1])     # 北=0, 东=pi/2；lat 视作 x


def angdiff(a, b):
    d = abs(a - b) % (2 * math.pi)
    return min(d, 2 * math.pi - d)


def analyze(fn, var, label):
    data = load(fn, var)
    lines = data["lines"]
    buckets = {"single": 0, "stub": 0, "corridor": 0, "real": 0}
    real_list = []
    for nm, v in lines.items():
        pls = [c for c in (v.get("polylines") or []) if c and len(c) >= 2]
        if len(pls) < 2:
            buckets["single"] += 1
            continue
        lens = [plen(c) for c in pls]
        # 全部段都很短 => 引轨残片簇，不是干线断裂
        if max(lens) < MINSIG:
            buckets["stub"] += 1
            continue
        # 判定缺口时忽略碎屑段（< SLIVER km）：它们是引轨/残渣，
        # 拿来和主干配对会造出「几百公里的假缺口」（实测吸收后残留 0.0~1.5km 碎屑）。
        big = [k for k in range(len(pls)) if lens[k] >= SLIVER]
        if len(big) < 2:
            # 只有一段是干线，其余全是碎屑 → 实质连续
            if len(pls) > 1:
                buckets["corridor"] += 1
            else:
                buckets["single"] += 1
            continue
        # 找主干段之间的最近端点对 + 该处的走向一致性
        best = None
        for i in big:
            for j in big:
                if j <= i:
                    continue
                for ei in (0, 1):
                    for ej in (0, 1):
                        pa, ta = endpoint(pls[i], float(ei))
                        pb, tb = endpoint(pls[j], float(ej))
                        d = hav(pa, pb)
                        if best is None or d < best[0]:
                            cosang = abs(ta[0] * tb[0] + ta[1] * tb[1])
                            best = (d, i, j, ei, ej, cosang)
        d, i, j, ei, ej, cosang = best
        if d <= THRESH:
            buckets["corridor"] += 1                      # 端点够近，自动缝合已覆盖
        elif cosang >= CORRIDOR:
            buckets["real"] += 1                # 同一走廊被截断 → 真缺口，值得补
            real_list.append({
                "line": nm, "gapKm": round(d, 1), "cos": round(cosang, 2),
                "segA": round(lens[i], 1), "segB": round(lens[j], 1), "segs": len(pls),
                "a": [round(x, 4) for x in endpoint(pls[i], float(ei))[0]],
                "b": [round(x, 4) for x in endpoint(pls[j], float(ej))[0]],
            })
        else:
            buckets["corridor"] += 1     # 走向不一致 => 并行线/同名异地，不是缺口
    return buckets, real_list


def main():
    as_json = "--json" in sys.argv
    out = {}
    for fn, var, label in [("highspeed_lines.js", "HSLINES", "高铁"),
                           ("conventional_lines.js", "CONVLINES", "普速")]:
        b, rl = analyze(fn, var, label)
        out[label] = {"buckets": b, "real": sorted(rl, key=lambda x: -x["gapKm"])}
        if as_json:
            continue
        total = sum(b.values())
        print("=" * 62)
        print("%s：共 %d 条线路" % (label, total))
        print("  单段完整            : %4d" % b["single"])
        print("  引轨残片簇(非干线)  : %4d" % b["stub"])
        print("  自动可连/非真断裂   : %4d" % b["corridor"])
        print("  >> 真缺口(需人工)  : %4d" % b["real"])
        if rl:
            print("  真缺口明细（按间距降序，前 12）:")
            for r in sorted(rl, key=lambda x: -x["gapKm"])[:12]:
                print("     %-16s 间距%6.1fkm 走向cos=%.2f 段长%6.1f/%6.1fkm 共%d段"
                      % (r["line"][:16], r["gapKm"], r["cos"], r["segA"], r["segB"], r["segs"]))
        print()
    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        tot = sum(out[k]["buckets"]["real"] for k in out)
        print(">>> 全网真缺口合计：%d 条（这些才是管理员真正要动手的）" % tot)


if __name__ == "__main__":
    main()
