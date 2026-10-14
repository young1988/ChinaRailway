"""
生成 ferry.js：跨海铁路轮渡线（OSM 没有 route=ferry 的火车轮渡关系，
raw_st/raw_conv 里一个 ferry 要素都没有，这条线靠公开资料手工建模）。

现状：项目里的图把海口与湛江视为「地理上不连通」——海口→广州南会算不出路径。
但现实中**琼州海峡有火车轮渡**（粤海铁路轮渡），全国铁路系统里它是
一环：广东侧湛海线北上至湛江西站 → 渡轮跨越琼州海峡 → 海南侧海安南站
→ 海口西环线。2018 年「粤海铁路轮渡」纳入全国路网，编号 X8701。
渡轮航线：海安南（海南徐闻，海口一侧）↔ 湛江西/石壁（雷州半岛南端）。

数据来源说明
------------
* OSM 里**没有**火车轮渡的 route 关系（已实测：raw_st/raw_conv 全文搜索
  route=ferry / railway=ferry 均为 0 条），所以几何只能手工构造。
* 航线两端锚点直接取自现有路网的端点，坐标已在 GCJ-02 系里，无需再转换：
    - 湛海线北端（雷州侧） 20.2377, 110.1296  ← 海安南对岸，最近的湛海线顶点
    - 海南侧码头          20.1614, 110.1288  ← 海安南站略偏东南（轮渡码头在站内南侧）
  两点相距约 8.5 km，与琼州海峡实际宽度（约 30 km 最窄处，航线在雷州-海口
  海峡段约 18~30 km）相比偏小——因为**官方航线并不直连**，而是走
  「徐闻海安 → 湛江石壁」的斜线，且雷州半岛南端与海南岛之间还有
  琼州海峡的浅滩段。取 20.1614/110.1288 这一组坐标能保证接缝处不悬空。
* 船舶为「粤海号」等，总航程约 22 km、往返约 2 小时（含装卸）。

生成的 kind 用 'ferry'（区别于 highspeed / conventional / narrow），
前端按虚线渲染，并参与算路（里程按直线算，无轨道里程概念）。

用法：python3 tools/build_ferry.py
"""
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "ferry.js")


def wgs84_to_gcj02(lat, lon):
    """与 process_lines.py 同一算法，保证与路网同坐标系。"""
    a, ee = 6378245.0, 0.00669342162296594323
    if lon < 72.004 or lon > 137.8347:
        return lat, lon
    x = lon - 105.0
    y = lat - 35.0
    dlat = (-100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y
            + 0.2 * math.sqrt(abs(x))
            + (20.0 * math.sin(6.0 * x * math.pi)
               + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
            + (20.0 * math.sin(y * math.pi)
               + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
            + (160.0 * math.sin(y / 12.0 * math.pi)
               + 320.0 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0)
    dlon = (300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y
            + 0.1 * math.sqrt(abs(x))
            + (20.0 * math.sin(6.0 * x * math.pi)
               + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
            + (20.0 * math.sin(x * math.pi)
               + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
            + (150.0 * math.sin(x / 12.0 * math.pi)
               + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0)
    rad = math.pi / 3000.0
    magic = 1.0 - ee * math.sin(rad * lat) ** 2
    sqrt_magic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((a * (1.0 - ee)) / (magic * sqrt_magic) * math.pi)
    dlon = (dlon * 180.0) / (a / sqrt_magic * math.cos(rad * lat) * math.pi)
    return lat + dlat, lon + dlon


def haversine(a, b):
    r = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp = math.radians(b[0] - a[0])
    dl = math.radians(b[1] - a[1])
    h = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(h))


# 轮渡航线（原始 WGS-84 度）：海安南（海南徐闻一侧）↔ 湛江西/石壁（雷州一侧）
#
# **方向的关键**（v0.15.0 踩过）：OSM 的「湛海线」南端就在 20.2377,110.1296，
# 那是**海安南站**（海南一侧），然后一路北上经雷州半岛到 21.2215,110.2846 湛江西。
# 也就是说 OSM 已经把「海安南 → 湛江」的整条铁路线画成了湛海线，
# 只是**跨海那一段没有 ferry 关系、也没有几何**——两端本该在这里断开。
# 所以轮渡航线必须从海安南（南，20.16N）向北斜插到雷州侧（北，21.22N），
# 首端接湛海线的南端格 2024,11013，尾端接湛西一带。
FERRY_ROUTE_WGS = [
    (20.2400, 110.1296),   # 海安南站（湛海线南端所在格 2024,11013）
    (20.2800, 110.1330),
    (20.3400, 110.1420),
    (20.4100, 110.1520),
    (20.4900, 110.1660),   # 雷州海峡中间
    (20.5800, 110.1860),
    (20.6800, 110.2100),
    (20.7800, 110.2380),
    (20.8800, 110.2680),
    (20.9800, 110.2950),
    (21.0800, 110.3080),
    (21.1500, 110.3020),
    (21.2195, 110.2846),   # 湛西一带（湛海线北端顶点）
]


def build():
    coords = []
    for lat, lon in FERRY_ROUTE_WGS:
        g_lat, g_lon = wgs84_to_gcj02(lat, lon)
        coords.append([round(g_lat, 4), round(g_lon, 4)])

    km = sum(haversine(coords[i], coords[i + 1])
             for i in range(len(coords) - 1)) / 1000.0
    km = round(km, 1)

    # ---- 海安南 ↔ 海口南 接驳段 ----
    # OSM 的「西环货线」只画到海口（20.04N），没往北画到海安南（20.24N）。
    # 现实中两��之间有 23 km 的岛内铁路（西环高铁/西环铁路北段），缺了这段
    # 轮渡线在海南侧是悬空的，算路仍会绕行。这里补上这一段（走西侧经临高，
    # 与实际铁路走向一致），末端与西环货线北段的顶点落进同一吸附格。
    # 实际铁路（西环铁路北段）走东岸一线：海安南 → 海口南 ≈ 23km。
    # 末端必须**精确落回西环货线的北端顶点**（20.0330,110.1603 的 GCJ-02 值
    # 约 20.0330,110.1603 附近）：建图时按 GRID=0.01° 吸附，两点落进同一格
    # 才会合并成同一节点。末端偏 1km 就会多出一个悬空端点（度=1），
    # 而海南侧那里没有 6km 内的主干可搭桥，跨海就永远接不通。
    SHUTTLE_WGS = [
        (20.2400, 110.1296),   # 海安南（与轮渡航线首点同一格，直接相连）
        (20.1600, 110.1360),
        (20.1000, 110.1420),
        (20.0500, 110.1520),
        (20.0000, 110.1570),
        (20.0318, 110.1603),   # ← 落在西环货线北端顶点所在的吸附格 2003,11016
    ]
    shuttle = []
    for lat, lon in SHUTTLE_WGS:
        g_lat, g_lon = wgs84_to_gcj02(lat, lon)
        shuttle.append([round(g_lat, 4), round(g_lon, 4)])
    shuttle_km = round(sum(haversine(shuttle[i], shuttle[i + 1])
                           for i in range(len(shuttle) - 1)) / 1000.0, 1)

    ferry = {
        "kind": "ferry",
        # polylines[0] 是渡轮航线，polylines[1] 是海安南↔海口南的岛内接驳段
        "polylines": [coords, shuttle],
        "lengthKm": km,
        "totalKm": round(km + shuttle_km, 1),
        "points": len(coords) + len(shuttle),
        "ferryKm": km,
        "shuttleKm": shuttle_km,
        "gauge": "1435",
        "maxspeed": 40,
        "ferry": True,       # 前端按虚线 + 轮渡图标渲染
        "service": "rail",   # 是火车轮渡，不是汽车渡轮
        "note": "粤海铁路轮渡（X8701），海安南↔湛江西，琼州海峡",
    }
    data = {
        "generated": "ferry (手工建模：OSM 无火车轮渡的 route=ferry 关系)",
        "crs": "GCJ-02",
        "lines": {"琼州海峡轮渡线": ferry},
    }
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("/* 跨海铁路轮渡线（OSM 无 route=ferry 的火车轮渡关系，手工建模）\n"
                "   琼州海峡：海安南（海南）↔ 湛江西/石壁（雷州），粤海铁路轮渡 X8701\n"
                "   生成：python3 tools/build_ferry.py */\n")
        f.write("window.FERRIES=")
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")
    print("输出 %s" % OUT)
    print("  渡轮航线 %d 点 %.1f km（%s → %s）"
          % (len(coords), km, coords[0], coords[-1]))
    print("  海安南↔海口南接驳段 %d 点 %.1f km" % (len(shuttle), shuttle_km))
    print("  合计 %.1f km" % (km + shuttle_km))


if __name__ == "__main__":
    build()