"""이미 구운 MBTiles 팩에서 낮은 줌만 뽑아 작은 개요 팩을 만든다.

네트워크를 쓰지 않는다. 원본 팩(예: 전국 z0–14, 1 GB)을 그대로 장비에 넣기에는 커서,
"어디쯤에 있는지"만 보여 주는 저해상도 전국 팩을 잘라 낼 때 쓴다. 자세한 구역 팩과 함께
두면 `MapPackSet`이 줌 범위로 골라 쓴다(README 참조).

사용:
    python tools/shrink_map_pack.py asset/maps/korea-z14.mbtiles \\
        --out asset/maps/korea-z11.mbtiles --max-zoom 11
"""
import argparse
import sqlite3
import sys
from pathlib import Path


def shrink(source_path, out_path, min_zoom, max_zoom, name=None):
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    metadata = dict(source.execute("SELECT name, value FROM metadata").fetchall())
    if metadata.get("format", "").lower() in ("pbf", "mvt"):
        source.close()
        sys.exit("벡터 팩입니다. 먼저 tools/rasterize_pack.py로 래스터로 구우세요.")
    if out_path.exists():
        out_path.unlink()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out = sqlite3.connect(out_path)
    out.executescript("""
        CREATE TABLE metadata (name text, value text);
        CREATE TABLE tiles (zoom_level integer, tile_column integer, tile_row integer, tile_data blob);
        CREATE UNIQUE INDEX tile_index ON tiles (zoom_level, tile_column, tile_row);
    """)
    # 줌 범위만 바꾼다. bounds·attribution은 원본 그대로 둬야 출처 표기가 유지된다.
    metadata["minzoom"], metadata["maxzoom"] = str(min_zoom), str(max_zoom)
    metadata["name"] = name or f"{metadata.get('name', source_path.stem)} 개요"
    if "center" in metadata:
        parts = metadata["center"].split(",")
        if len(parts) == 3:
            metadata["center"] = f"{parts[0]},{parts[1]},{max_zoom}"
    out.executemany("INSERT INTO metadata VALUES (?, ?)", list(metadata.items()))
    rows = source.execute(
        "SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles "
        "WHERE zoom_level BETWEEN ? AND ?", (min_zoom, max_zoom))
    written = 0
    while True:
        batch = rows.fetchmany(2000)
        if not batch:
            break
        out.executemany("INSERT OR REPLACE INTO tiles VALUES (?, ?, ?, ?)", batch)
        written += len(batch)
    out.commit()
    out.execute("VACUUM")
    out.close()
    source.close()
    return written, out_path.stat().st_size


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="원본 .mbtiles")
    parser.add_argument("--out", type=Path, required=True, help="생성할 .mbtiles")
    parser.add_argument("--min-zoom", type=int, default=0)
    parser.add_argument("--max-zoom", type=int, default=11)
    parser.add_argument("--name", default=None, help="새 팩 이름 (기본: 원본 이름 + ' 개요')")
    args = parser.parse_args()
    if not args.source.is_file():
        sys.exit(f"원본을 찾을 수 없습니다: {args.source}")
    written, size = shrink(args.source, args.out, args.min_zoom, args.max_zoom, args.name)
    print(f"{args.out} · z{args.min_zoom}–{args.max_zoom} · {written}장 · {size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
