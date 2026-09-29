"""오프라인 타일팩 읽기.

MBTiles 1.3(SQLite 단일 파일)의 **래스터** 팩만 읽는다. 벡터 타일은 색·선 굵기가 없어
스타일과 렌더러가 있어야 그림이 되므로, 굽는 단계에서 래스터로 만들어 온다
(`tools/build_map_pack.py`, `tools/rasterize_pack.py`). 앱은 네트워크를 사용하지 않는다.
MBTiles의 tile_row는 TMS 규약(아래가 0)이라 XYZ 규약(위가 0)과 y축이 반대다.
"""
import sqlite3
from pathlib import Path

# 이 값이 나오면 벡터 팩이다. metadata에 format이 없는 구형 래스터 팩은 그대로 받아들인다.
VECTOR_FORMATS = ("pbf", "mvt")


class EmptyTileSource:
    """타일팩이 없을 때. 지도 배경 없이 좌표·궤적만 표시하는 폴백."""
    available = False
    min_zoom = 10
    max_zoom = 16
    attribution = ""
    center = None

    def tile(self, zoom, x, y):
        return None

    def close(self):
        pass


class MBTilesSource:
    def __init__(self, path):
        self.path = Path(path)
        # 읽기 전용으로 연다. 앱이 타일팩을 수정할 일은 없고, 실수로 저널 파일을 만들지 않는다.
        self._connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
        metadata = dict(self._connection.execute("SELECT name, value FROM metadata").fetchall())
        self.name = metadata.get("name", self.path.stem)
        image_format = metadata.get("format", "").lower()
        if image_format in VECTOR_FORMATS:
            self._connection.close()
            raise ValueError(f"래스터 팩이 아니라 벡터 타일({image_format})입니다")
        self.min_zoom = int(metadata.get("minzoom", 0))
        self.max_zoom = int(metadata.get("maxzoom", 22))
        self.attribution = metadata.get("attribution", "")
        bounds = metadata.get("bounds")
        self.bounds = tuple(float(part) for part in bounds.split(",")) if bounds else None
        self.available = True

    @property
    def center(self):
        """GPS fix 이전에 지도를 어디에 둘지. (위도, 경도)."""
        if self.bounds is None:
            return None
        min_lon, min_lat, max_lon, max_lat = self.bounds
        return ((min_lat + max_lat) / 2, (min_lon + max_lon) / 2)

    def tile(self, zoom, x, y):
        row = self._connection.execute(
            "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
            (zoom, x, (1 << zoom) - 1 - y)).fetchone()
        return row[0] if row else None

    def close(self):
        self._connection.close()


class MapPackSet:
    """디렉터리의 모든 .mbtiles를 묶어 하나의 타일 소스로 제공한다.

    구역을 추가할 때 코드를 바꾸지 않고 파일만 넣으면 된다.
    """

    def __init__(self, packs):
        self.packs = tuple(packs)
        self.available = bool(self.packs)
        self.min_zoom = min((pack.min_zoom for pack in self.packs), default=10)
        self.max_zoom = max((pack.max_zoom for pack in self.packs), default=16)
        self.attribution = next((pack.attribution for pack in self.packs if pack.attribution), "")
        self.center = next((pack.center for pack in self.packs if pack.center), None)

    @classmethod
    def load(cls, directory):
        directory = Path(directory)
        packs, failures = [], []
        for path in sorted(directory.glob("*.mbtiles")) if directory.is_dir() else ():
            try:
                packs.append(MBTilesSource(path))
            except (sqlite3.Error, ValueError) as error:
                # 손상되거나 종류가 다른 팩 하나가 나머지 구역까지 막지 않도록 건너뛴다.
                failures.append((path, error))
        return cls(packs), failures

    def tile(self, zoom, x, y):
        for pack in self.packs:
            if pack.min_zoom <= zoom <= pack.max_zoom:
                data = pack.tile(zoom, x, y)
                if data is not None:
                    return data
        return None

    def close(self):
        for pack in self.packs:
            pack.close()
