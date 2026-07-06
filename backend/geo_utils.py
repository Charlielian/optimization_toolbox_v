"""
基础地理计算模块
- Vincenty椭球距离(支持近海100km+场景)
- 方位角计算
- 扇区多边形生成
- 扇区交叠面积计算
- 方位角夹角(用于预过滤)
- 方向性背向判断 (PCI 冲突过滤用)
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from shapely.geometry import Polygon, mapping
from shapely.ops import unary_union

# WGS84椭球参数
WGS84_A = 6378137.0           # 长半轴(米)
WGS84_F = 1 / 298.257223563   # 扁率
WGS84_B = WGS84_A * (1 - WGS84_F)
WGS84_E2 = 1 - (WGS84_B ** 2) / (WGS84_A ** 2)  # 第一偏心率平方

# 场景模式:land(陆地) / offshore(近海)
SCENE_MODE = "land"

# 场景默认参数
SCENE_PRESETS = {
    "land":    {"default_max_distance_km": 5.0,  "default_beamwidth": 65, "default_radius_m": 500},
    "offshore":{"default_max_distance_km": 50.0, "default_beamwidth": 90, "default_radius_m": 30000},
}


def vincenty_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Vincenty椭球距离(米) - WGS84
    反向求解,最大迭代200次,精度1e-12
    """
    if lat1 == lat2 and lon1 == lon2:
        return 0.0

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    L = math.radians(lon2 - lon1)

    U1 = math.atan((1 - WGS84_F) * math.tan(phi1))
    U2 = math.atan((1 - WGS84_F) * math.tan(phi2))
    sinU1, cosU1 = math.sin(U1), math.cos(U1)
    sinU2, cosU2 = math.sin(U2), math.cos(U2)

    lam = L
    for _ in range(200):
        sin_lam = math.sin(lam)
        cos_lam = math.cos(lam)
        sin_sigma = math.sqrt((cosU2 * sin_lam) ** 2 +
                              (cosU1 * sinU2 - sinU1 * cosU2 * cos_lam) ** 2)
        if sin_sigma == 0:
            return 0.0
        cos_sigma = sinU1 * sinU2 + cosU1 * cosU2 * cos_lam
        sigma = math.atan2(sin_sigma, cos_sigma)
        sin_alpha = cosU1 * cosU2 * sin_lam / sin_sigma
        cos2_alpha = 1 - sin_alpha ** 2
        if cos2_alpha == 0:
            cos2_sigma_m = 0
        else:
            cos2_sigma_m = cos_sigma - 2 * sinU1 * sinU2 / cos2_alpha
        C = WGS84_F / 16 * cos2_alpha * (4 + WGS84_F * (4 - 3 * cos2_alpha))
        lam_prev = lam
        lam = L + (1 - C) * WGS84_F * sin_alpha * (
            sigma + C * sin_sigma * (cos2_sigma_m + C * cos_sigma *
                                     (-1 + 2 * cos2_sigma_m ** 2))
        )
        if abs(lam - lam_prev) < 1e-12:
            break
    else:
        # 近乎反平行,使用Haversine兜底
        return haversine_distance(lat1, lon1, lat2, lon2)

    u2 = cos2_alpha * (WGS84_A ** 2 - WGS84_B ** 2) / (WGS84_B ** 2)
    A = 1 + u2 / 16384 * (4096 + u2 * (-768 + u2 * (320 - 175 * u2)))
    B = u2 / 1024 * (256 + u2 * (-128 + u2 * (74 - 47 * u2)))
    dsigma = B * sin_sigma * (cos2_sigma_m + B / 4 * (
        cos_sigma * (-1 + 2 * cos2_sigma_m ** 2) -
        B / 6 * cos2_sigma_m * (-3 + 4 * sin_sigma ** 2) * (-3 + 4 * cos2_sigma_m ** 2)
    ))
    s = WGS84_B * A * (sigma - dsigma)
    return s


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine兜底距离(米)"""
    R = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def bearing_angle(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    真方位角(0-360,正北为0,顺时针)
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlam = math.radians(lon2 - lon1)
    y = math.sin(dlam) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    theta = math.degrees(math.atan2(y, x))
    return (theta + 360) % 360


def angle_diff(a: float, b: float) -> float:
    """两个方位角的最小夹角(0-180)"""
    d = abs(a - b) % 360
    return min(d, 360 - d)


def _get_azimuth_and_beam(c: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    """
    从 cell 字典中读取方位角与波束宽度。
    返回 (azimuth°, beamwidth°) 或 None (缺失则 None, 不默认填值)。
    优先级:
      - azimuth: c['azimuth']
      - beamwidth: c['beamwidth'] 或 c['beam']
    """
    try:
        az = c.get("azimuth")
        bw = c.get("beamwidth", c.get("beam"))
        if az is None or bw is None:
            return None
        az_f = float(az)
        bw_f = float(bw)
        if bw_f <= 0 or bw_f > 360:
            return None
        return az_f % 360.0, bw_f
    except (TypeError, ValueError):
        return None


def is_back_facing(c: Dict[str, Any], other: Dict[str, Any]) -> bool:
    """
    判断 c 的扇区主瓣是否背向 other (单方向)。
    - 取 c 的 azimuth + beamwidth
    - 计算 c->other 的真方位角
    - 若 other 落在 c 主瓣之外 (angle_diff > beamwidth/2) 则 True
    缺失任一必要字段返回 False (保守, 不过滤)。
    """
    params = _get_azimuth_and_beam(c)
    if params is None:
        return False
    az, bw = params
    try:
        lat1 = float(c["lat"]); lon1 = float(c["lon"])
        lat2 = float(other["lat"]); lon2 = float(other["lon"])
    except (KeyError, TypeError, ValueError):
        return False
    if lat1 == lat2 and lon1 == lon2:
        return False
    bearing = bearing_angle(lat1, lon1, lat2, lon2)
    return angle_diff(az, bearing) > bw / 2.0


def mutual_back_facing(c: Dict[str, Any], other: Dict[str, Any]) -> bool:
    """
    双向背向: c 看不到 other 且 other 也看不到 c。
    只有双向背向才完全豁免 (严格模式, 避免单向背向误豁免)。
    """
    return is_back_facing(c, other) and is_back_facing(other, c)


def destination_point(lat: float, lon: float, distance_m: float, bearing_deg: float) -> Tuple[float, float]:
    """
    给定起点、距离、方位角,求终点经纬度
    """
    ang_dist = distance_m / WGS84_A
    bearing = math.radians(bearing_deg)
    phi1 = math.radians(lat)
    lam1 = math.radians(lon)
    phi2 = math.asin(math.sin(phi1) * math.cos(ang_dist) +
                     math.cos(phi1) * math.sin(ang_dist) * math.cos(bearing))
    lam2 = lam1 + math.atan2(
        math.sin(bearing) * math.sin(ang_dist) * math.cos(phi1),
        math.cos(ang_dist) - math.sin(phi1) * math.sin(phi2)
    )
    return math.degrees(phi2), math.degrees(lam2)


def sector_polygon(lon: float, lat: float, azimuth: float, beamwidth: float,
                   radius_m: float, n: int = 36) -> Polygon:
    """
    生成扇区多边形(Shapely Polygon)
    :param lon: 经度
    :param lat: 纬度
    :param azimuth: 中心方位角(正北,顺时针)
    :param beamwidth: 波束宽度(度),如65表示扇区张角±32.5°
    :param radius_m: 覆盖半径(米)
    :param n: 弧线采样点数
    """
    azimuth = azimuth % 360
    start = (azimuth - beamwidth / 2) % 360
    end = (azimuth + beamwidth / 2) % 360

    coords: List[Tuple[float, float]] = [(lon, lat)]

    sweep = beamwidth
    if start + sweep > 360:
        # 跨越0度
        steps1 = max(1, int(n * (360 - start) / sweep))
        for i in range(steps1 + 1):
            b = start + i * (360 - start) / steps1
            plat, plon = destination_point(lat, lon, radius_m, b % 360)
            coords.append((plon, plat))
        steps2 = max(1, n - steps1)
        for i in range(1, steps2 + 1):
            b = i * end / steps2
            plat, plon = destination_point(lat, lon, radius_m, b)
            coords.append((plon, plat))
    else:
        steps = max(2, int(n))
        for i in range(steps + 1):
            b = start + i * sweep / steps
            plat, plon = destination_point(lat, lon, radius_m, b)
            coords.append((plon, plat))

    return Polygon(coords)


def sector_overlap_area(poly1: Polygon, poly2: Polygon) -> float:
    """
    两个扇区多边形的交叠面积(平方米)
    """
    if not poly1.is_valid:
        poly1 = poly1.buffer(0)
    if not poly2.is_valid:
        poly2 = poly2.buffer(0)
    if poly1.intersects(poly2):
        return poly1.intersection(poly2).area
    return 0.0


def poly_to_geojson(poly: Polygon) -> dict:
    """Shapely Polygon -> GeoJSON dict"""
    return mapping(poly)


def build_sector(lon: float, lat: float, azimuth: float, beamwidth: float,
                 radius_m: float) -> dict:
    """
    一站式生成扇区GeoJSON,供前端Leaflet渲染
    """
    poly = sector_polygon(lon, lat, azimuth, beamwidth, radius_m)
    return {
        "type": "Feature",
        "properties": {"azimuth": azimuth, "beamwidth": beamwidth, "radius_m": radius_m},
        "geometry": mapping(poly),
    }


def merge_overlaps(polygons: List[Polygon]) -> dict:
    """合并多个多边形为单个GeoJSON"""
    if not polygons:
        return {"type": "FeatureCollection", "features": []}
    merged = unary_union([p.buffer(0) if not p.is_valid else p for p in polygons])
    return mapping(merged)


def get_scene_defaults() -> dict:
    """获取当前场景的默认参数"""
    return SCENE_PRESETS.get(SCENE_MODE, SCENE_PRESETS["land"])


def set_scene_mode(mode: str) -> None:
    """设置场景模式"""
    global SCENE_MODE
    if mode in SCENE_PRESETS:
        SCENE_MODE = mode


def point_in_polygon(lat: float, lon: float, ring: List[List[float]]) -> bool:
    """
    射线法判断点是否在闭合多边形内。
    ring: [[lat, lon], ...] 至少 3 个顶点。
    """
    n = len(ring)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        lat_i, lon_i = float(ring[i][0]), float(ring[i][1])
        lat_j, lon_j = float(ring[j][0]), float(ring[j][1])
        if ((lat_i > lat) != (lat_j > lat)) and (
            lon < (lon_j - lon_i) * (lat - lat_i) / (lat_j - lat_i + 1e-15) + lon_i
        ):
            inside = not inside
        j = i
    return inside


def point_in_area(lat: float, lon: float, area: Optional[Dict[str, Any]]) -> bool:
    """
    判断 (lat, lon) 是否落在前端圈选区域内。
    area: rect | circle | polygon
      polygon: {type:'polygon', points:[[lat,lon],...]}
    """
    if not area:
        return True
    t = area.get("type")
    if t == "rect":
        if area.get("lat1") is None or area.get("lat2") is None:
            return True
        la1, la2 = min(area["lat1"], area["lat2"]), max(area["lat1"], area["lat2"])
        lo1, lo2 = min(area["lon1"], area["lon2"]), max(area["lon1"], area["lon2"])
        return la1 <= lat <= la2 and lo1 <= lon <= lo2
    if t == "circle":
        if area.get("radius_km") is None:
            return True
        lat0, lon0 = float(area["lat"]), float(area["lon"])
        return haversine_distance(lat, lon, lat0, lon0) / 1000.0 <= float(area["radius_km"])
    if t == "polygon":
        pts = area.get("points") or []
        return point_in_polygon(lat, lon, pts)
    return True