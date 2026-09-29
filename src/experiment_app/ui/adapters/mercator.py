"""WGS84 (EPSG:4326) 위경도와 Web Mercator (EPSG:3857) 타일 픽셀 좌표 변환.

XYZ 타일 규약을 따른다. 원점은 좌상단이고 줌 z에서 세계는 256 * 2^z 픽셀이다.
UI_SPEC.md 4.3의 '내부 지도 엔진이 다른 투영을 요구하면 map adapter에서 변환한다'를 담당한다.
"""
import math

TILE_SIZE = 256
# Web Mercator는 극지방을 표현할 수 없다. 정사각 세계 지도가 되는 위도에서 자른다.
MAX_LATITUDE = 85.05112877980659


def world_size(zoom):
    # 핀치/휠 줌은 정수 사이의 값을 지나므로 비트시프트를 쓸 수 없다.
    return TILE_SIZE * 2.0 ** zoom


def lonlat_to_pixel(lon, lat, zoom):
    size = world_size(zoom)
    sin = math.sin(math.radians(max(-MAX_LATITUDE, min(MAX_LATITUDE, lat))))
    return ((lon + 180.0) / 360.0 * size,
            (0.5 - math.log((1 + sin) / (1 - sin)) / (4 * math.pi)) * size)


def pixel_to_lonlat(x, y, zoom):
    size = world_size(zoom)
    return (x / size * 360.0 - 180.0,
            math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / size)))))


def ground_resolution(lat, zoom):
    """줌 레벨의 픽셀당 지상 거리(m). 타일팩 해상도 상한을 판단할 때 쓴다."""
    return 156543.03392804097 * math.cos(math.radians(lat)) / 2.0 ** zoom


def zoom_about(center, offset, old_zoom, new_zoom):
    """앵커를 고정한 채 줌을 바꿨을 때의 새 중심 (위도, 경도).

    offset은 화면 중심에서 앵커까지의 픽셀 변위다. 확대해도 커서(또는 핀치 중심)
    아래의 지점이 같은 화면 위치에 남는다.
    """
    x, y = lonlat_to_pixel(center[1], center[0], old_zoom)
    anchor_lon, anchor_lat = pixel_to_lonlat(x + offset[0], y + offset[1], old_zoom)
    anchor_x, anchor_y = lonlat_to_pixel(anchor_lon, anchor_lat, new_zoom)
    lon, lat = pixel_to_lonlat(anchor_x - offset[0], anchor_y - offset[1], new_zoom)
    return (lat, lon)
