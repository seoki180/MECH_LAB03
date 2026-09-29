"""래스터 XYZ 타일 서버에서 타일을 받아 오프라인 MBTiles 타일팩을 만든다.

앱은 래스터 팩만 읽는다(`infrastructure/tiles.py`). tilemaker 등으로 만든 벡터 팩은
색·선 굵기가 없어 스타일과 렌더러를 거쳐야 그림이 되므로, 여기서 한 번 구워 둔다.
벡터 소스는 렌더러가 오버줌하므로 z14 벡터팩으로 z18 래스터까지 뽑을 수 있다.

네트워크가 있는 PC에서 한 번 실행하고, 생성된 .mbtiles를 `asset/maps/`에 넣는다.
앱은 이 스크립트를 import하지 않으며 런타임에 네트워크를 쓰지 않는다.

준비 (tileserver-gl이 OSM 벡터팩을 래스터로 렌더링한다):
    docker run --rm -d --name tileserver \\
        -v "$PWD/tools/tileserver:/data" -v "$PWD/build:/data/mbtiles" \\
        -p 8080:8080 maptiler/tileserver-gl

사용:
    python tools/rasterize_pack.py --bbox 127.09,37.39,127.11,37.41 \\
        --name "데모 구역" --out asset/maps/demo-site.mbtiles
    docker stop tileserver
"""
import argparse
import math
import sys
import urllib.error
import urllib.request
from pathlib import Path

from build_map_pack import coordinates, open_pack

DEFAULT_URL = "http://localhost:8080/styles/osm-bright/{z}/{x}/{y}.png"
# OSM 데이터는 ODbL이라 표기가 필수다. 지도 위 표기는 팩 metadata에서 읽어 그린다.
OSM_ATTRIBUTION = "© OpenStreetMap contributors"


def tile_range(bbox, zoom):
    """bbox를 덮는 XYZ 타일 범위 (x0, x1, y0, y1). 표준 슬리피맵 공식."""
    size = 1 << zoom

    def x_tile(lon):
        return int((lon + 180.0) / 360.0 * size)

    def y_tile(lat):
        radians = math.radians(lat)
        return int((1 - math.log(math.tan(radians) + 1 / math.cos(radians)) / math.pi) / 2 * size)

    return x_tile(bbox[0]), x_tile(bbox[2]), y_tile(bbox[3]), y_tile(bbox[1])


def fetch(url, timeout):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        # 렌더러가 빈 타일에 404를 주기도 한다. 구역 가장자리에서는 정상이다.
        if error.code == 404:
            return None
        raise


def build(args):
    pack = open_pack(args.out, args.name, args.bbox, args.min_zoom, args.max_zoom,
                     image_format=args.format, attribution=args.attribution,
                     description=f"{args.name} · 래스터 타일팩")
    written = skipped = 0
    try:
        for zoom in range(args.min_zoom, args.max_zoom + 1):
            x0, x1, y0, y1 = tile_range(args.bbox, zoom)
            for x in range(x0, x1 + 1):
                for y in range(y0, y1 + 1):
                    data = fetch(args.url.format(z=zoom, x=x, y=y), args.timeout)
                    if not data:
                        skipped += 1
                        continue
                    pack.execute("INSERT OR REPLACE INTO tiles VALUES (?, ?, ?, ?)",
                                 (zoom, x, (1 << zoom) - 1 - y, data))
                    written += 1
                    # 한 줌이 12만 장을 넘기도 한다. 줌이 끝날 때까지 침묵하지 않는다.
                    if written % 2000 == 0:
                        print(f"z{zoom} · {written}장", flush=True)
            pack.commit()
            print(f"z{zoom} 완료 · 누적 {written}장")
    except urllib.error.URLError as error:
        sys.exit(f"타일 서버에 연결하지 못했습니다 ({error.reason}).\n"
                 f"{args.url}\ndocker 컨테이너가 떠 있는지 확인하세요.")
    finally:
        pack.commit()
        pack.close()
    size = args.out.stat().st_size / 1e6
    print(f"\n{args.out} · {written}장 · {size:.1f} MB" + (f" · 타일 없음 {skipped}장" if skipped else ""))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bbox", type=coordinates, required=True, help="minlon,minlat,maxlon,maxlat (WGS84)")
    parser.add_argument("--out", type=Path, required=True, help="생성할 .mbtiles 경로")
    parser.add_argument("--url", default=DEFAULT_URL, help="{z}/{x}/{y}를 포함한 래스터 타일 URL")
    parser.add_argument("--name", default="site", help="타일팩 이름")
    parser.add_argument("--min-zoom", type=int, default=13)
    parser.add_argument("--max-zoom", type=int, default=18)
    parser.add_argument("--format", default="png", help="타일 이미지 형식. metadata에 기록한다")
    parser.add_argument("--attribution", default=OSM_ATTRIBUTION, help="지도에 표기할 출처")
    parser.add_argument("--timeout", type=float, default=30.0)
    build(parser.parse_args())


if __name__ == "__main__":
    main()
