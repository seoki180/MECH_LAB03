"""번들 자원과 외부 자료 폴더를 점검한다.

앱 안에 두는 이유: 번들된 .exe 안에서 실행돼야 의미가 있다. 개발 환경에서 통과해도
번들에 asset이 빠졌거나 지도 폴더가 번들 내부를 가리키면 배포본에서만 깨진다.

실행: MECHLab.exe --self-check  또는  uv run python -m experiment_app --self-check
"""
import json
import os
import sys
from pathlib import Path

from experiment_app import paths
from experiment_app.infrastructure.tiles import MapPackSet


def run(report=None):
    # 모듈 전역이 아니라 호출마다 새로 만든다. 전역이면 두 번 호출할 때 결과가 누적된다.
    results = []
    notes = []

    def check(condition, message):
        results.append((bool(condition), message))
        return bool(condition)

    info = paths.describe()

    # 1. 글꼴과 아이콘은 번들 안(또는 개발 저장소 안)에 있어야 한다.
    fonts = sorted(paths.asset_dir("fonts").glob("Pretendard-*.otf"))
    check(len(fonts) >= 3, f"번들 글꼴 {len(fonts)}개를 찾음 (asset/fonts)")
    icons = sorted(paths.asset_dir("icons").glob("*.svg")) + sorted(paths.asset_dir("icons").glob("*.png"))
    check(len(icons) >= 10, f"번들 아이콘 {len(icons)}개를 찾음 (asset/icons)")

    # 2. 지도 폴더는 반드시 번들 밖이어야 한다. 번들 안이면 팩을 교체할 수 없다.
    map_dir = paths.map_pack_dir()
    inside_bundle = paths.is_frozen() and str(map_dir).startswith(str(paths.bundle_root()))
    check(not inside_bundle, f"지도 폴더가 번들 밖에 있음: {map_dir}")
    # 환경 변수로 공용 드라이브를 지정했다면 .exe 옆이 아닌 것이 정상이므로 검사하지 않는다.
    if paths.is_frozen() and not os.environ.get(paths.MAP_PACK_ENV):
        check(map_dir.parent == Path(sys.executable).resolve().parent,
              "지도 폴더가 실행 파일과 같은 폴더에 있음")

    # 3. 타일팩이 실제로 열리는지. 없으면 배경 없이 동작해야 하며 오류가 아니다.
    tiles, failures = MapPackSet.load(map_dir)
    check(not failures, f"열지 못한 타일팩 없음 (실패 {len(failures)}건)")
    if tiles.available:
        check(tiles.tile(15, 0, 0) is not None or tiles.center is not None,
              "타일팩에서 메타데이터/타일을 읽음")
        notes.append(f"지도: {len(tiles.packs)}개 팩, zoom {tiles.min_zoom}-{tiles.max_zoom}, 중심 {tiles.center}")
    else:
        notes.append(f"지도: 팩 없음 (배경 없이 동작). 찾은 위치: {map_dir}")
    tiles.close()

    # 4. 시험 폴더는 실행 파일 밖에 있어야 사용자가 시험을 주고받을 수 있다.
    tests_root = paths.tests_dir()
    inside_bundle = paths.is_frozen() and str(tests_root).startswith(str(paths.bundle_root()))
    check(not inside_bundle, f"시험 폴더가 번들 밖에 있음: {tests_root}")
    if tests_root.is_dir():
        groups = [c for c in tests_root.iterdir() if c.is_dir() and not c.name.startswith(".")]
        files = sorted(tests_root.glob("*/*.json"))
        missing = [p.name for p in files if not p.with_suffix(".csv").is_file()]
        notes.append(f"시험: 시험목록 {len(groups)}개, 시험 {len(files)}개 ({tests_root})")
        if missing:
            notes.append(f"시나리오 없는 시험(실행 불가): {', '.join(missing)}")
    else:
        notes.append(f"시험: 폴더 없음 (첫 실행 시 생성). 위치: {tests_root}")

    # 5. NMEA는 없으면 앱이 안내와 함께 멈추므로 상태만 보고한다.
    notes.append(f"NMEA: {paths.default_nmea() or '없음 — nmea 폴더에 .nmea를 넣으세요'}")

    lines = list(notes)
    lines.append(json.dumps({k: str(v) for k, v in info.items()}, ensure_ascii=False, indent=2))
    lines += [("  OK  " if ok else "  실패 ") + message for ok, message in results]
    failed = [m for ok, m in results if not ok]
    lines.append(f"실패 {len(failed)}건" if failed else f"{len(results)}건 모두 통과")

    from experiment_app.bootstrap import emit
    emit("\n".join(lines), report)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
