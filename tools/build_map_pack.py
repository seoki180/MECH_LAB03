"""Sentinel-2 위성영상으로 오프라인 MBTiles 타일팩을 만든다.

네트워크가 있는 PC에서 한 번 실행하고, 생성된 .mbtiles를 실험 장비의 `asset/maps/`에 복사한다.
앱은 이 스크립트를 import하지 않으며 런타임에 네트워크를 쓰지 않는다.

데이터는 Earth Search v1(AWS Open Data)에서 가져온다. 인증이 필요 없고,
COG 부분 읽기로 관심 영역만 받으므로 씬 전체를 내려받지 않는다.
각 씬의 `visual` 자산은 이미 10m 3밴드 8비트 RGB(TCI)라 밴드 합성이 필요 없다.

Sentinel-2는 10m/px다. 위도 37도 기준 z14가 원본 수준이고 z16이 4배 확대다.
그보다 높은 줌을 구워도 정보량은 늘지 않으므로 기본 상한을 16으로 둔다.

설치:
    uv pip install --python .venv/bin/python -e '.[maptools]'

사용:
    python tools/build_map_pack.py --bbox 127.09,37.39,127.11,37.41 --name "시험장" \\
        --out asset/maps/site.mbtiles
"""
import argparse
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path

STAC_SEARCH = "https://earth-search.aws.element84.com/v1/search"
COLLECTION = "sentinel-2-l2a"
ATTRIBUTION = "Contains modified Copernicus Sentinel data"
# 한 씬이 관심 영역을 다 덮지 못할 때(MGRS 경계 걸침) 대비해 여러 씬을 순서대로 시도한다.
MAX_SCENES = 4


def search_scenes(bbox, start, end, max_cloud):
    body = json.dumps({
        "collections": [COLLECTION],
        "bbox": list(bbox),
        "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z",
        "query": {"eo:cloud_cover": {"lt": max_cloud}},
        "limit": 50,
    }).encode()
    request = urllib.request.Request(STAC_SEARCH, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        features = json.load(response)["features"]
    features.sort(key=lambda feature: feature["properties"].get("eo:cloud_cover", 100))
    return features[:MAX_SCENES]


def open_pack(path, name, bbox, min_zoom, max_zoom, image_format="jpg",
              attribution=ATTRIBUTION, description=""):
    """빈 MBTiles를 만들고 metadata를 채운다. tools/rasterize_pack.py도 이 스키마를 쓴다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE metadata (name text, value text);
        CREATE TABLE tiles (zoom_level integer, tile_column integer, tile_row integer, tile_data blob);
        CREATE UNIQUE INDEX tile_index ON tiles (zoom_level, tile_column, tile_row);
    """)
    connection.executemany("INSERT INTO metadata VALUES (?, ?)", [
        ("name", name), ("format", image_format), ("type", "baselayer"), ("version", "1.1"),
        ("bounds", ",".join(str(value) for value in bbox)),
        ("center", f"{(bbox[0] + bbox[2]) / 2},{(bbox[1] + bbox[3]) / 2},{max_zoom}"),
        ("minzoom", str(min_zoom)), ("maxzoom", str(max_zoom)),
        ("attribution", attribution), ("description", description),
    ])
    connection.commit()
    return connection


def build(args):
    import morecantile
    from rio_tiler.errors import TileOutsideBounds
    from rio_tiler.io import Reader

    scenes = search_scenes(args.bbox, args.start, args.end, args.max_cloud)
    if not scenes:
        sys.exit("조건에 맞는 씬이 없습니다. 기간을 넓히거나 --max-cloud를 올리세요.")
    for scene in scenes:
        print(f"씬 {scene['id']} · 구름 {scene['properties'].get('eo:cloud_cover', 0):.1f}%")

    tms = morecantile.tms.get("WebMercatorQuad")
    readers = [Reader(scene["assets"]["visual"]["href"], tms=tms) for scene in scenes]
    pack = open_pack(args.out, args.name, args.bbox, args.min_zoom, args.max_zoom,
                     description=f"Sentinel-2 L2A true colour · {args.name}")
    written = skipped = 0
    try:
        for zoom in range(args.min_zoom, args.max_zoom + 1):
            for tile in tms.tiles(*args.bbox, [zoom]):
                data = render_tile(readers, tile, TileOutsideBounds)
                if data is None:
                    skipped += 1
                    continue
                pack.execute("INSERT OR REPLACE INTO tiles VALUES (?, ?, ?, ?)",
                             (zoom, tile.x, (1 << zoom) - 1 - tile.y, data))
                written += 1
            pack.commit()
            print(f"z{zoom} 완료 · 누적 {written}장")
    finally:
        pack.commit()
        pack.close()
        for reader in readers:
            reader.close()
    size = args.out.stat().st_size / 1e6
    print(f"\n{args.out} · {written}장 · {size:.1f} MB" + (f" · 영상 없음 {skipped}장" if skipped else ""))


def render_tile(readers, tile, outside_bounds):
    for reader in readers:
        try:
            image = reader.tile(tile.x, tile.y, tile.z)
        except outside_bounds:
            continue
        # 씬 경계 밖은 마스크가 0이다. 절반 이상 비면 다음 씬을 시도한다.
        if image.mask is not None and image.mask.mean() < 128:
            continue
        return image.render(add_mask=False, img_format="JPEG", quality=85)
    return None


def coordinates(value):
    parts = [float(part) for part in value.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("minlon,minlat,maxlon,maxlat 형식이어야 합니다")
    return tuple(parts)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bbox", type=coordinates, required=True, help="minlon,minlat,maxlon,maxlat (WGS84)")
    parser.add_argument("--out", type=Path, required=True, help="생성할 .mbtiles 경로")
    parser.add_argument("--name", default="site", help="타일팩 이름")
    parser.add_argument("--min-zoom", type=int, default=10)
    parser.add_argument("--max-zoom", type=int, default=16, help="Sentinel-2 해상도상 16 초과는 의미가 없다")
    parser.add_argument("--start", default="2024-04-01", help="검색 시작일 YYYY-MM-DD")
    parser.add_argument("--end", default="2024-10-31", help="검색 종료일 YYYY-MM-DD")
    parser.add_argument("--max-cloud", type=float, default=10.0, help="허용 구름량 %%")
    build(parser.parse_args())


if __name__ == "__main__":
    main()
