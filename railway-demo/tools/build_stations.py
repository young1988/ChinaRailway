#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 raw_st/ 的 OSM 车站节点生成「带稳定 ID」的车站数据 + 别名层（方案 C）。

为什么用 OSM node id 当稳定 ID：
  node id 是 OSM 分配给地理实体的永久编号，只认实体不认名字——
  车站改名（通化 → 新通化）id 不变，重抓数据 id 也不变。
  这样「旧行程按名字写在老站位上」的错位问题就能根治：
  旧名进aliases 层，仍能解析回同一个 id、拿到最新坐标。

关键处理：
  1. 按 OSM id 去重（省 bbox 重叠，同一节点会被多个省抓到）
  2. 剔除境外车站：名字必须含汉字 + 坐标落在中国境内
  3. 名字归一化：取最长汉字段，去掉后缀「站」，原名与 alt_name 的中文别名进别名层
  4. 同名不同地全部保留（同 grid 的重复节点合并），由前端按上下文消歧

输出（放在 railway-demo/ 根目录，index.html 按序引入）：
  stations_all.js    window.STATIONS_ALL  "id|名字|纬度|经度\n..."
  stations_real.js   window.STATIONS_REAL { id: [lat, lon] }   （applyRealStations 用）
  aliases.js         window.STATIONS_ALIASES { 旧名/别名: id }（resolveStation 用）
"""
import glob
import json
import os
import re
from collections import defaultdict

from process_lines import wgs84_to_gcj02   # 与线路几何用同一套 GCJ-02 转换

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw_st")
ROOT = os.path.dirname(HERE)

# 中国境内大致范围（含中国台湾/中国香港/中国澳门）
LAT_MIN, LAT_MAX = 17.0, 54.5
LON_MIN, LON_MAX = 73.0, 135.5

CJK = re.compile(r"[㐀-鿿]+")   # 含扩展 A 区，覆盖繁体
TRAIL_STATION = re.compile(r"站$")


def norm_name(raw):
    """取最长汉字段；返回 (主名, 归一化前的原汉字串或 None)"""
    runs = CJK.findall(raw or "")
    if not runs:
        return None, None
    base = max(runs, key=len)
    if len(base) < 2:
        return None, None
    name = TRAIL_STATION.sub("", base)
    if len(name) < 2:               # 「站」这种被剥光的，退回原汉字串
        name = base
    alt = base if base != name else None
    return name, alt


def has_cjk(s):
    return bool(s) and bool(CJK.search(s))


def load():
    """按 OSM id 去重，返回 {id: element}"""
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
            nid = e.get("id")
            if nid is not None:
                by_id[nid] = e
    return by_id


def collect_aliases(tags, main_name):
    """从 alt_name / name:zh 等标签里提取中文别名（不含英文/俄/朝文）"""
    alts = set()
    raw_alt = tags.get("alt_name")
    if raw_alt:
        for part in str(raw_alt).split(";"):
            part = part.strip()
            # 只取纯汉字段（排除「XX站;English;Русский」里的非中文项）
            if has_cjk(part) and part != main_name:
                alts.add(part)
    for key in ("name:zh", "name:zh-Hans", "name:zh-Hant"):
        v = (tags.get(key) or "").strip()
        if has_cjk(v) and v != main_name:
            alts.add(v)
    return alts


def main():
    nodes = load()
    print("去重后节点: %d" % len(nodes))

    foreign = 0
    noname = 0
    merged = 0
    seen = {}                 # (name, lat1, lon1) -> id   ~11km 网格，合并重复节点
    records = []              # [(id, name, lat, lon)]
    id2name = {}              # id -> 主名（用于别名去重）
    alias_map = {}            # 别名 -> id

    # 先遍历，保证 seen 用到的是「首次出现的 id」作代表
    items = sorted(nodes.values(), key=lambda e: e.get("id"))

    for e in items:
        nid = e.get("id")
        tags = e.get("tags") or {}
        raw = (tags.get("name") or "").strip()
        lat, lon = e.get("lat"), e.get("lon")
        if nid is None or not raw or lat is None or lon is None:
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
            # 同网格重复节点：把它的 id 也登记成同一主名的别名，保证旧引用仍能解析
            merged += 1
            continue
        seen[key] = nid
        records.append((nid, name, lat, lon))
        id2name[nid] = name

        # 归一化前的原名（带「站」）作为别名
        if alt:
            alias_map.setdefault(alt, nid)
        # alt_name / name:zh 里的中文别名
        for a in collect_aliases(tags, name):
            alias_map.setdefault(a, nid)

    print("剔除境外/无汉字名: %d   无坐标/无id: %d   合并重复: %d"
          % (foreign, noname, merged))
    print("站点记录: %d   唯一主名: %d   别名: %d"
          % (len(records), len(set(id2name.values())), len(alias_map)))

    # 过滤掉「别名与某个主名冲突且指向不同 id」的项——主名优先，别名让位
    name2ids = defaultdict(set)
    for nid, nm in id2name.items():
        name2ids[nm].add(nid)
    conflict = 0
    clean_alias = {}
    for a, aid in alias_map.items():
        if a in name2ids and aid not in name2ids[a]:
            conflict += 1          # 别名同时是别人的主名 → 丢弃，保住主名准确
            continue
        clean_alias[a] = aid
    if conflict:
        print("别名与主名冲突(丢弃): %d" % conflict)

    # ---- 输出 stations_all.js：id|名字|纬度|经度 ----
    # 坐标系：OSM 原始坐标是 WGS-84，而本项目底图（腾讯地图）与线路几何都是 GCJ-02。
    # 两套坐标系在中国境内相差 480~620 m（方向随纬度变），不转换会让所有车站标记
    # 系统性偏离轨道与真实站位——本溪站就曾因此偏西约 500 m（v0.12.1 修复）。
    # 去重/筛选仍用原始 WGS-84 坐标，只在落盘前转换。
    rows = []
    for nid, nm, lat, lon in sorted(records, key=lambda r: r[1]):
        glat, glon = wgs84_to_gcj02(lat, lon)
        rows.append("%d|%s|%.4f|%.4f" % (nid, nm, glat, glon))
    with open(os.path.join(ROOT, "stations_all.js"), "w", encoding="utf-8") as f:
        f.write("/* 全国铁路车站表（OSM railway=station/halt，已转 GCJ-02）\n"
                "   格式：id|名字|纬度|经度，按行分隔；\n"
                "   id 为 OSM node id（稳定不变，改名不换id）；同名不同地会有多行，由前端按上下文消歧 */\n")
        f.write("window.STATIONS_ALL=" + json.dumps("\n".join(rows), ensure_ascii=False) + ";\n")

    # ---- 输出 stations_real.js：id -> [lat, lon] ----
    real = {}
    for nid, nm, lat, lon in records:
        glat, glon = wgs84_to_gcj02(lat, lon)
        real[str(nid)] = [round(glat, 4), round(glon, 4)]
    with open(os.path.join(ROOT, "stations_real.js"), "w", encoding="utf-8") as f:
        f.write("/* 车站坐标表：OSM node id -> [纬度, 经度]（GCJ-02），按 id 覆盖坐标用 */\n")
        f.write("window.STATIONS_REAL=" + json.dumps(real, ensure_ascii=False) + ";\n")

    # ---- 输出 aliases.js：别名/旧名 -> id ----
    with open(os.path.join(ROOT, "aliases.js"), "w", encoding="utf-8") as f:
        f.write("/* 车站别名层：旧名/别名 -> OSM node id。\n"
                "   车站更名后旧名留在这里，旧行程靠它仍能解析回同一 id、拿到最新坐标。\n"
                "   由 build_stations.py 生成，请勿手改（手改会在重生成时丢失）。 */\n")
        f.write("window.STATIONS_ALIASES=" + json.dumps(clean_alias, ensure_ascii=False) + ";\n")

    for fn in ("stations_all.js", "stations_real.js", "aliases.js"):
        size = os.path.getsize(os.path.join(ROOT, fn))
        print("输出 %-18s %7.0f KB" % (fn, size / 1024))


if __name__ == "__main__":
    main()
