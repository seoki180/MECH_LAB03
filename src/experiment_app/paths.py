"""파일 경로 해석을 한 곳에 모은다.

개발 실행과 PyInstaller로 묶은 .exe 실행의 경로 규칙이 다르므로, 경로를 쓰는 쪽이
`sys.frozen`이나 `__file__`을 직접 다루지 않도록 이 모듈만 보게 한다.

두 종류를 구분한다:

- **번들 자원**: 글꼴·아이콘처럼 앱과 함께 배포되는 읽기 전용 파일. .exe 안에 들어가며
  실행 시 PyInstaller가 임시 폴더(`sys._MEIPASS`)에 푼다. 사용자가 손대지 않는다.
- **외부 자료**: 지도 타일팩·NMEA 로그·저장 데이터처럼 .exe 밖에 두는 파일.
  지도 팩은 전국 단위가 1 GB를 넘어 실행 파일에 넣을 수 없고, 구역을 추가할 때
  앱을 다시 빌드하지 않아야 하므로 반드시 바깥 폴더에서 읽는다.

외부 자료의 기준 폴더(`external_root()`)는 .exe가 있는 폴더이고, 개발 실행에서는
저장소 루트다. 따라서 배포 폴더 구조는 다음과 같다::

    MECHLab/
      MECHLab.exe
      maps/            ← 지도 타일팩(.mbtiles)을 여기에 넣는다
      nmea/            ← 재생할 .nmea 로그
      .mechlab/        ← 앱이 만드는 저장 데이터

각 경로는 환경 변수로 덮어쓸 수 있어, 지도 팩을 공용 드라이브에 두고 여러 PC가
같은 폴더를 보게 할 수 있다.
"""
import os
import sys
from pathlib import Path

# 환경 변수 이름. 명령줄 인자가 있으면 그쪽이 우선한다.
MAP_PACK_ENV = "MECHLAB_MAP_PACK"
NMEA_ENV = "MECHLAB_NMEA"
DATA_DIR_ENV = "MECHLAB_DATA_DIR"
TESTS_DIR_ENV = "MECHLAB_TESTS_DIR"

# 여러 .nmea가 있을 때 고르는 기본 로그. 개발 환경에서 쓰던 파일을 유지한다.
PREFERRED_NMEA = "10km_log.nmea"


def is_frozen():
    """PyInstaller로 묶인 실행 파일에서 동작 중인지."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def bundle_root():
    """번들 자원(글꼴·아이콘)의 기준 폴더."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS"))
    # src/experiment_app/paths.py → 저장소 루트
    return Path(__file__).resolve().parents[2]


def external_root():
    """외부 자료(지도·NMEA·저장 데이터)의 기준 폴더."""
    override = os.environ.get("MECHLAB_HOME")
    if override:
        return Path(override).expanduser()
    if is_frozen():
        # sys.executable은 임시 해제 폴더가 아니라 사용자가 실행한 .exe를 가리킨다.
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def asset_dir(name):
    """번들 자원 하위 폴더: asset/<name>."""
    return bundle_root() / "asset" / name


def _first_existing(candidates, predicate):
    for path in candidates:
        if predicate(path):
            return path
    return None


def map_pack_dir():
    """지도 타일팩 폴더. 없으면 첫 후보를 돌려주어 오류 문구에 경로를 보여준다.

    `.mbtiles`가 실제로 들어 있는 폴더를 고른다. 빈 폴더가 먼저 있다고 해서
    타일팩이 든 폴더를 건너뛰지 않는다.
    """
    override = os.environ.get(MAP_PACK_ENV)
    if override:
        return Path(override).expanduser()
    root = external_root()
    candidates = [root / "maps", root / "asset" / "maps"]
    found = _first_existing(candidates, lambda p: p.is_dir() and any(p.glob("*.mbtiles")))
    return found or candidates[0]


def data_dir():
    """앱이 쓰는 저장 폴더(기록, 결과)."""
    override = os.environ.get(DATA_DIR_ENV)
    return Path(override).expanduser() if override else external_root() / ".mechlab"


def tests_dir():
    """시험 정의 폴더. 실행 파일 밖에 두어 사용자가 직접 관리한다.

    구조는 ``test/<시험목록 폴더>/<시험 이름>.json`` 이고, 같은 폴더의 같은 이름
    ``.csv`` 가 그 시험의 시험시나리오다. 앱을 다시 빌드하지 않고 파일을 주고받아
    시험을 옮길 수 있어야 하므로 번들 안이 아니라 바깥 폴더에서 읽고 쓴다.
    """
    override = os.environ.get(TESTS_DIR_ENV)
    return Path(override).expanduser() if override else external_root() / "test"


def default_nmea():
    """재생할 기본 .nmea 파일. 후보 폴더에서 첫 파일을 고르며 없으면 None."""
    override = os.environ.get(NMEA_ENV)
    if override:
        return Path(override).expanduser()
    root = external_root()
    for directory in (root / "nmea", root / "asset" / "nmea", root / "asset"):
        if not directory.is_dir():
            continue
        # 이름순으로 고정해 실행할 때마다 다른 파일이 잡히지 않게 한다.
        found = sorted(directory.rglob("*.nmea"))
        if not found:
            continue
        # 개발 환경에서 쓰던 기본 로그가 있으면 그대로 유지한다.
        return next((p for p in found if p.name == PREFERRED_NMEA), found[0])
    return None


def describe():
    """시작 로그와 오류 문구에 쓰는 현재 경로 요약."""
    return {
        "frozen": is_frozen(),
        "bundle_root": bundle_root(),
        "external_root": external_root(),
        "map_pack_dir": map_pack_dir(),
        "data_dir": data_dir(),
        "tests_dir": tests_dir(),
        "nmea": default_nmea(),
    }
