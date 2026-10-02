# PyInstaller 빌드 사양.
#
# 번들에 넣는 것: 앱 코드와 asset/fonts, asset/icons. 이 파일들은 읽기 전용이고
# 크기가 작아 실행 파일 안에 두는 편이 배포가 단순하다.
#
# 번들에 넣지 않는 것: asset/maps 타일팩. 전국 팩이 1 GB를 넘어 실행 파일에 넣을 수
# 없고, 구역을 추가할 때마다 앱을 다시 빌드하지 않아야 한다. 앱은 experiment_app.paths
# 규칙에 따라 .exe 옆 maps 폴더(또는 MECHLAB_MAP_PACK 환경 변수)에서 읽는다.
# .nmea 로그와 저장 데이터도 같은 이유로 바깥에 둔다.
#
# 빌드: pyinstaller packaging/mechlab.spec --noconfirm
from pathlib import Path

# SPECPATH는 PyInstaller가 넣어주는 이 spec 파일의 폴더다.
ROOT = Path(SPECPATH).resolve().parent

analysis = Analysis(
    # __main__.py가 아니라 이 진입 스크립트를 쓴다. 이유는 packaging/entry.py 설명 참고.
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    # (원본, 번들 안 경로). paths.bundle_root() 기준 상대 경로와 맞춘다.
    datas=[
        (str(ROOT / "asset" / "fonts"), "asset/fonts"),
        (str(ROOT / "asset" / "icons"), "asset/icons"),
    ],
    hiddenimports=["websockets", "websockets.sync.client"],
    hookspath=[],
    runtime_hooks=[],
    # 앱은 네트워크를 쓰지 않고 wx만 의존한다. 테스트·빌드 전용 패키지는 빼서 크기를 줄인다.
    excludes=["pytest", "rio_tiler", "morecantile", "tkinter", "numpy", "PIL"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="MECHLab",
    debug=False,
    strip=False,
    upx=False,
    # GUI 앱이라 콘솔 창을 띄우지 않는다. --print-paths 확인이 필요하면
    # 명령 프롬프트에서 MECHLab.exe --print-paths로 실행하고 출력은 파이프로 받는다.
    console=False,
    disable_windowed_traceback=False,
)
# onefile 빌드다. EXE에 binaries와 datas를 직접 넘기고 COLLECT를 쓰지 않으면
# 단일 .exe가 나온다. (`onefile=` 같은 인자는 없다.) 실행 시 임시 폴더에 풀리며
# paths.bundle_root()가 그 폴더를 가리킨다.
