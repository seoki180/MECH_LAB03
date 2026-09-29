# MECHLab-03

`UI_SPEC.md` 기반 Python/wxPython 실험 관리 앱의 첫 데모 구현입니다.
실장비를 연결하지 않고 Test 편집 → 저장 → 실험 준비 → 수집/중지 → 결과 조회를 실행할 수 있습니다.

## 실행

현재 프로젝트의 `.venv`에는 설치가 완료되어 있습니다.

```sh
.venv/bin/python -m experiment_app
```

새 환경에서는 Python 3.14 이상으로 가상환경을 만든 뒤 설치합니다.

```sh
python3.14 -m venv .venv
uv pip install --python .venv/bin/python -e '.[dev]'
.venv/bin/python -m experiment_app
```

IDE 실행 진입점: `src/experiment_app/__main__.py`, 모듈 실행 권장: `experiment_app`.
기본 데이터는 프로젝트의 `.mechlab/`에 저장합니다. `--data-dir <경로>`로 저장 위치를 지정할 수 있습니다.
화면 글꼴은 `asset/fonts/`의 Pretendard(SIL OFL, `LICENSE.txt`)를 실행 시 앱 프로세스에만 등록해 사용합니다(macOS는 CoreText, 그 외는 `wx.Font.AddPrivateFont`). 등록에 실패하면 시스템 글꼴로 표시합니다.
지도 타일팩은 `asset/maps/`에서 읽고 `--map-pack <디렉터리>`로 바꿀 수 있습니다.
기본 측정 입력은 제공된 `10km_log.nmea`입니다. `--nmea <파일>` 또는 장치 탭의 `NMEA 파일 선택`으로 바꿀 수 있습니다.
파일 변경은 실험 창을 닫은 뒤 적용되며, 다음 세션이 파일 처음부터 재생됩니다.
현재 확인 환경은 macOS, Python 3.14.0, wxPython 4.3.1입니다. Windows/Linux 및 배포 패키징은 아직 검증하지 않았습니다.

## 사용할 수 있는 기능

- 동일한 헤더를 유지하는 Tests / 장치 / 결과 페이지, 별도 비모달 실험 창 1개.
- 데모 그룹 2개, Test 6개. 그룹/Test 생성·복제·삭제, 검색, 그룹 내 순서 변경.
- 모든 Test에서 `point_angle`(Zero/Accel/Brake 각 2개), `calibration_data`(2개), `limit_point`(Accel/Brake), `zero_brake_angle`을 편집·저장. 이름·메모도 편집 가능하며(반복 횟수는 설정 화면에서 제외) 필드별 검증, dirty 초안, 저장/버리기/취소 흐름을 제공합니다.
- stable ID 기반 부분 수정, 서비스의 편집 정책 검사, revision 충돌 검출, 원자적 로컬 JSON 저장.
- 실행 snapshot 고정, 중복 시작 차단, 비동기 중지·기록 정리, 별도 세션 ID로 재실행.
- 실험 입력 데이터는 저장된 Test와 실행 snapshot 및 측정 기록 첫 행에 포함됩니다. NMEA 값은 Test 설정값에서 계산하지 않습니다.
- 제공된 NovAtel `#INSPVAXA` 기록의 위치, INS 위치 품질, 북/동 속도를 재생합니다. 첫 유효 fix가 시작점입니다. 실험 화면은 지도 아래에 동쪽 이동·북쪽 이동·고도·상승/하강을 2×2 카드로 동시에 표시합니다. 속도와 시작점 직선거리도 계속 수집·기록합니다. GPS 지도에도 시작점과 이동량을 표시합니다.
- 수집/계산 50 Hz, 화면 10 Hz, GPS 지도 1 Hz, 파일 끝 또는 최대 30초에 자동 완료합니다. JSONL에는 화면용 측정 샘플과 실행 snapshot을 저장합니다. 원본 `.nmea` 문장 자체는 재기록하지 않습니다.
- `INS_RTKFIXED` 등 파일의 위치 품질을 표시합니다. 일반 NMEA 좌표를 RTK 보정 좌표로 변환하거나 실제 보정 신호를 생성하지 않습니다.
- 네트워크를 사용하지 않는 오프라인 래스터 지도. 핀치·휠·버튼 줌(z13–17), 기본 자동 추적, 드래그 이동 시 추적 일시 중단·현재 위치 버튼으로 복귀 및 추적 재개, fix 손실 시 마지막 유효 위치/시각 유지.
- 최신 표시 채널 전체 또는 특정 영역 TSV 복사. 전체 복사는 GPS 행 포함, 영역 복사는 해당 센서 채널만 포함.
- 모든 채널의 원본 JSONL 기록과 실행 snapshot을 포함하는 결과 요약 저장/재시작 복원.
- 장치 탭의 NMEA 파일 선택과 정상 / GPS fix 손실 / 지도 배경 실패 / 기록 실패 / 저장 실패 시나리오.
- 메인 1100 DIP 미만에서는 목록/편집 전환, 실험 800 DIP 미만에서는 세로 본문 스크롤. 실행 헤더는 고정.

실험이 종료된 뒤 창을 닫고 다시 `실험 열기`를 누르면 새 세션을 준비합니다.
저장 실패 시나리오에서 저장이 실패하면 장치 탭을 `정상`으로 바꾼 뒤 저장을 재시도하세요.
센서 지연/단절·값 오류 시나리오는 가상 소스에서만 적용됩니다. 실제 필수 채널 정책과 허용범위는 장비 연동 전에 확정해야 합니다.

## 오프라인 지도

지도는 로컬 MBTiles 타일팩만 읽고 앱 실행 중 네트워크를 쓰지 않습니다.
**래스터 팩(JPEG/PNG)만 읽습니다.** 벡터 타일(`format=pbf`)은 색·선 굵기가 없어 스타일과
렌더러가 있어야 그림이 되므로, 굽는 단계에서 래스터로 만들어 넣습니다.

타일팩은 Git에 포함하지 않습니다(`asset/maps/*.mbtiles` 제외). 용량이 크고 아래 절차로 언제든
다시 구울 수 있기 때문입니다. **클론 직후에는 지도 배경 없이 격자로 뜨므로 팩을 먼저 굽습니다.**
구역을 추가할 때도 팩을 굽고 `asset/maps/`에 넣기만 하면 됩니다. 코드 수정은 필요 없습니다.

`MapPackSet`이 디렉터리의 팩을 전부 열어 줌 범위로 골라 쓰므로, 넓은 개요 팩과 좁고 자세한 구역 팩을
같이 두면 겹쳐 동작합니다. 권장 구성은 **전국 z0–14 개요 팩 + 시험 구역 z13–17 팩**입니다.

화면에 허용하는 줌은 `ui/adapters/tile_map.py`의 `MIN_ZOOM, MAX_ZOOM`(z13–17)이 정합니다. 팩이
가진 범위를 그대로 쓰지 않는 이유는, 같은 줌이라도 구역에 따라 타일이 있기도 없기도 해서 합쳐진
줌 범위가 실제로 볼 수 있는 범위를 말해 주지 못하기 때문입니다. 더 확대해서 쓰려면 구역 팩을
그 줌까지 굽고 `MAX_ZOOM`을 올립니다.

### OSM 지도 (도로·건물)

OSM 원본(`*.osm.pbf`)을 tilemaker로 벡터 타일팩으로 만든 뒤, tileserver-gl로 렌더링해 래스터로 굽습니다.
전국 벡터팩 하나면 국내 어느 구역이든 여기서 뽑을 수 있어 `.osm.pbf`를 다시 쓸 일은 없습니다.
벡터 소스는 렌더러가 오버줌하므로 z14 벡터팩으로 z18 래스터까지 선명하게 나옵니다.

```sh
# 1. 렌더링 재료 — 벡터팩은 build/에, 폰트는 tools/tileserver/fonts/에 둡니다 (둘 다 Git 제외).
curl -L -o /tmp/noto-sans.zip https://github.com/openmaptiles/fonts/releases/download/v2.0/noto-sans.zip
unzip -q /tmp/noto-sans.zip "Noto Sans Regular/*" "Noto Sans Bold/*" "Noto Sans Italic/*" \
    -d tools/tileserver/fonts

# 2. 렌더링 서버. config.json이 build/의 벡터팩을 osm-bright 스타일로 그립니다.
docker run --rm -d --name tileserver \
    -v "$PWD/tools/tileserver:/data" -v "$PWD/build:/data/mbtiles" \
    -p 8090:8080 maptiler/tileserver-gl

# 3. 타일 한 장을 먼저 눈으로 확인한 뒤 전체를 굽습니다.
curl -sf -o /tmp/t.png "http://localhost:8090/styles/osm-bright/16/55904/25414.png" && file /tmp/t.png
.venv/bin/python tools/rasterize_pack.py \
    --url 'http://localhost:8090/styles/osm-bright/{z}/{x}/{y}.png' \
    --bbox 127.09,37.39,127.11,37.41 --min-zoom 13 --max-zoom 18 \
    --name "시험장" --out asset/maps/site.mbtiles
docker stop tileserver
```

데모 구역(약 2km × 2km) z13–18이 399장 8.1MB입니다. OSM 데이터는 ODbL이라
`© OpenStreetMap contributors` 표기가 필수이며, 표기는 타일팩 metadata의 `attribution`에서 읽어 그립니다.

시험 구역 팩과 전국 개요 팩은 같은 서버에서 bbox와 줌만 바꿔 굽습니다.

```sh
.venv/bin/python tools/rasterize_pack.py \
    --url 'http://localhost:8090/styles/osm-bright/{z}/{x}/{y}.png' \
    --bbox 126.55,37.15,126.90,37.55 --min-zoom 13 --max-zoom 17 \
    --name "인천·화성 시험구역" --out asset/maps/incheon-hwaseong-z17.mbtiles
```

인천 남동구와 화성 장안 시험 지점을 모두 덮는 약 31 × 44 km 구역입니다. 3.2만 장 안팎이며
z18까지 올리면 장 수가 4배가 됩니다.

반경 수십 m 수준까지 확대해서 볼 구역은 z19까지 굽습니다. z19는 위도 37도에서 약 0.23 m/px라
화면 한 폭(약 640px)이 150 m 안팎입니다.

```sh
.venv/bin/python tools/rasterize_pack.py \
    --url 'http://localhost:8090/styles/osm-bright/{z}/{x}/{y}.png' \
    --bbox 126.752,37.216,126.796,37.260 --min-zoom 13 --max-zoom 19 \
    --name "화성 자동차안전연구원" --out asset/maps/hwaseong-katri-z19.mbtiles

.venv/bin/python tools/rasterize_pack.py \
    --url 'http://localhost:8090/styles/osm-bright/{z}/{x}/{y}.png' \
    --bbox 126.636,37.436,126.671,37.463 --min-zoom 13 --max-zoom 19 \
    --name "인하대학교 인근" --out asset/maps/inha-univ-z19.mbtiles
```

각각 약 3.9 × 4.9 km(7,360장 32MB), 약 3.1 × 3.0 km(3,611장 40MB)입니다.
화면 줌 상한 `ui/adapters/tile_map.py`의 `MAX_ZOOM`은 이 팩에 맞춰 19로 두었습니다.

```sh
.venv/bin/python tools/rasterize_pack.py \
    --url 'http://localhost:8090/styles/osm-bright/{z}/{x}/{y}.png' \
    --bbox 124.5,33.0,132.0,39.5 --min-zoom 0 --max-zoom 14 \
    --name "대한민국" --out asset/maps/korea-z14.mbtiles
```

약 16.8만 장 1.0GB, 굽는 데 30분 남짓 걸립니다. 줌마다 타일 수가 4배가 되므로 상한을 14로 끊습니다.
같은 범위를 z18까지 구우면 4,280만 장 108GB입니다. z15 이상은 실험장 구역 팩이 덮습니다.

### 위성영상

```sh
uv pip install --python .venv/bin/python -e '.[maptools]'
.venv/bin/python tools/build_map_pack.py --bbox 127.09,37.39,127.11,37.41 \
    --name "시험장" --out asset/maps/site.mbtiles
```

영상은 Earth Search v1(AWS Open Data)의 Sentinel-2 L2A `visual`(TCI) 자산에서 받습니다.
인증이 필요 없고 COG 부분 읽기로 관심 영역만 가져오므로 씬 전체를 내려받지 않습니다.
Copernicus 라이선스는 재배포·상업이용이 자유롭습니다.

Sentinel-2는 10m/px라 위도 37도 기준 z14가 원본 수준이고 z16이 4배 확대입니다.
그 이상은 저장해도 정보가 늘지 않으므로 기본 상한을 z16으로 두고, 더 확대하면 렌더러가 상위 줌 타일을 늘려 표시합니다.
수백 m 규모 실험장을 또렷하게 보려면 VWorld 정사영상이나 국토정보플랫폼 항공정사영상으로 타일팩만 다시 구우면 됩니다.
`MapTileSource` 포트를 거치므로 앱 코드는 바뀌지 않습니다.

### 넣기 전 확인

```sh
.venv/bin/python -c "
import sqlite3; c = sqlite3.connect('asset/maps/새팩.mbtiles')
print(dict(c.execute('SELECT name, value FROM metadata')))
print(c.execute('SELECT tile_data FROM tiles LIMIT 1').fetchone()[0][:4])
"
```

`format`이 `png`/`jpg`이고 바이트가 `\x89PNG` 또는 `\xff\xd8\xff`로 시작하면 정상입니다.
`pbf`면 앱이 로드 단계에서 거부하고 이유를 콘솔에 출력합니다.

타일팩이 없거나 손상되거나 종류가 달라도 앱은 실행됩니다. 지도 배경 없이 격자와 좌표·궤적을 표시하며,
이는 장치 탭의 `지도 배경 실패` 시나리오 및 GPS fix 손실과 각각 구별되는 상태입니다.

## 구조

`domain`은 불변 정의·세션·샘플, `application`은 검증·저장·수집·복사·결과 서비스,
`infrastructure`는 로컬 저장/NMEA 재생/가상 소스/기록기/타일팩, `presentation`은 초안·비동기 저장 및 ViewModel,
`ui`는 wx 화면과 클립보드/지도 어댑터입니다. 구체 구현의 조립은 `bootstrap.py`에서 합니다.
`tools/`는 앱이 import하지 않는 사전 준비 스크립트입니다. `build_map_pack.py`는 위성영상을,
`rasterize_pack.py`는 래스터 타일 서버를 받아 타일팩으로 굽고 MBTiles 스키마를 공유합니다.
무거운 의존성은 `[maptools]`로 분리했으며 `rasterize_pack.py`는 표준 라이브러리만 씁니다.

저장은 단일 I/O executor, 수집과 기록은 각각 별도 worker에서 실행합니다.
표시 버퍼는 채널별 최신값만 유지하고 GPS 궤적은 600개로 제한합니다. 원본 기록은 별도 bounded queue를 사용하며
가득 차거나 기록이 실패하면 조용히 데이터를 버리지 않고 세션을 ERROR로 종료합니다.
동시 앱 인스턴스 간 파일 잠금은 아직 지원하지 않으므로 동일 데이터 디렉터리는 앱 1개에서 사용합니다.

## 검증

```sh
.venv/bin/python -m pytest -q --basetemp=.cache/test-tmp
.venv/bin/python tests/gui_smoke.py
```

GUI 점검은 데스크탑 세션이 필요하고 별도 `artifacts/smoke-data/`를 사용합니다.
점검 결과는 `artifacts/gui-smoke.json`에 기록합니다. 실시간 클립보드 덮어쓰기는 하지 않고 TSV 내용을 검증합니다.
`artifacts/`와 `.mechlab/`은 Git에서 제외됩니다.

## 남은 범위

- 실제 센서/GPS 프로토콜, 장비 제어·동기화 계산, 실제 Runs 반복 제어와 자동 완료 조건.
- 고해상도 영상(VWorld/항공정사영상) 타일팩, 위성 배경 위 OSM 오버레이, 센서 보조 카드·다수 채널 페이지와 고급 결과 분석.
- 장비별 스키마/정밀도/timeout/필수 채널 설정. 현재 스키마와 소스는 데모 전용입니다.
- 실제 OS 클립보드 왕복, 실제 터치 하드웨어의 핀치 입력, Windows/Linux, OS 배율 100/150/200% 검증.
- 네이티브 GUI 자동 점검은 통과했지만 이 실행 환경의 화면 캡처가 검은 이미지로 반환되어 픽셀 단위 시각 검증은 미완료입니다.
  지도만은 오프스크린 비트맵으로 렌더링해 도로·건물·한글 라벨과 궤적이 겹쳐 그려지는 것을 확인했습니다.

`UI_SPEC.md`의 전체 U01–U18 또는 제품 기능 완료를 주장하는 단계는 아닙니다.
wx 크기 변환 및 스크롤 구성은 [wx.Window](https://docs.wxpython.org/wx.Window.html),
[ScrolledPanel](https://docs.wxpython.org/wx.lib.scrolledpanel.ScrolledPanel.html) 공식 API를 참고했습니다.


### 2026-09-28 · Test 설정과 편집 모드

한 Test에 기존 실험 입력 데이터와 AR Trapezoidal Step, PF Straight Line 로봇 설정을 함께 저장한다.
기존 입력 데이터와 숨겨진 단계 데이터는 보존하며, 이전 저장 파일의 로봇 설정은 미설정으로 읽는다.
두 로봇 설정은 이미지에서 보이는 항목만 지원하고 Advanced Setup·Set to Max·실제 로봇 전송은 포함하지 않는다.
Control과 Gear mode는 직접 입력한다. 숫자와 체크 옵션은 미설정이 가능하며 장비 제한은 미확정이다.
End X/Y는 직접 입력하며 좌표를 자동 계산하지 않는다. 설정은 실행 snapshot 및 결과 기록에 포함된다.
Test 선택·새 Test·복제는 조회 상태로 표시하며 수정 버튼을 눌러 편집한다.
저장 성공 또는 취소 후 잠그고, 저장 실패·revision 충돌 시 초안과 편집 상태를 보존한다.
취소는 편집 진입 전 정의로 복원한다. 새 Test·복제는 조회 상태에서도 기본값으로 저장할 수 있다.

로봇 폼은 사진 참고 배치로 표시한다. AR은 라벨·입력·단위를 가로 정렬하고 PF는 Straight Path의 시작 좌표, 거리/각도, 끝 좌표를 나란히 묶으며 좁은 폭에서는 묶음을 다음 줄로 배치한다. Test Settings는 별도 영역이다. 옵션은 기본 체크박스(체크/해제)로 표시하며(저장값이 없으면 해제로 표시), 수정 버튼으로 입력 잠금을 해제한다.

### 로봇 송수신 확장

기본 실행은 **데모 로봇**을 사용한다. 실제 장비로 네트워크/시리얼 데이터를 보내지 않는다.
`application/ports.py`의 `RobotTransport`를 구현한 어댑터를 `build_services(..., robot_transport=adapter)`로 주입하면 UI 수정 없이 연결할 수 있다. 실제 구현체의 조립은 `bootstrap.py`에서 한다.

- `connect(timeout, cancel)`: 제한 시간 내 연결. 실패/취소 시 어댑터가 구조화된 `AppError`를 발생시킨다.
- `exchange(command, timeout, cancel)`: `configure`, `start`, `stop`을 전송하고 `RobotReply`를 반환한다. 응답은 같은 session_id와 명령 sequence를 포함한다. configure의 성공은 장비의 설정 적용 확인을 의미한다.
- `receive()`: 비차단 방식으로 `RobotEvent` 하나 또는 None을 반환한다. 수신 이벤트는 세션 ID, 증가 sequence, 상태, 타임존 있는 UTC 수신 시각과 자유 형식 data를 포함한다. 오류/단절 상태는 `error`/`disconnected`로 전달한다.
- `disconnect(timeout)`: 제한 시간 내 연결 해제. 부분 연결 실패 후에도 안전하게 호출할 수 있어야 한다.

명령과 이벤트는 내부 Python 계약이며 실제 로봇의 wire protocol이 아니다. 어댑터에서 로봇 SDK/통신 프레임으로 변환한다. 단위·필수값·허용범위와 장비 응답의 상관관계 매핑도 장비 사양에 맞춰 구현해야 한다. 미설정 필드의 None을 실제 장비 명령으로 그대로 전송해서는 안 되며 어댑터가 지원 여부를 검증해야 한다.

시작 worker에서 연결 → 저장된 snapshot의 세 설정 전송 → 적용 응답 확인 → 시작 응답 확인 후 측정한다. 설정 payload는 test_id, test_revision, experiment_data, ar_trapezoidal_step, pf_straight_line이다. 각 명령은 복사된 데이터를 사용하며 편집 초안은 전송하지 않는다.
수신 이벤트는 기존 센서/GPS 기록과 함께 JSONL에 `kind: robot`으로 기록한다. data는 장비별 원본 정보이며 기존 측정 카드에 자동 매핑하지 않는다. 화면은 최신 상태와 응답만 제한된 주기로 갱신한다.

기본 명령 timeout은 3초, 실행 상태 수신 지연은 5초이며 교체 가능한 서비스 기본값이다. 어댑터는 timeout/취소를 반드시 준수하고 수신 버퍼를 제한하며, 초과/연결 실패를 숨기지 않아야 한다. 이전 세션/역순 이벤트는 현재 표시·기록에 반영하지 않는다. 자동 재전송은 하지 않는다.
종료 시 수집 취소 → 별도 취소 토큰으로 중지 명령/응답 확인 → 연결 해제 → 기록 종료 순서로 처리한다. GUI 스레드에서 통신이나 join을 실행하지 않는다. 명령 거부, 응답 불일치, 수신 지연, 중지/해제 실패는 실험 ERROR와 불완전 결과로 표시한다. 중지 실패 상태를 장비가 멈췄다는 확인으로 해석하면 안 된다.

Test 설정 화면은 목록·본문·구역 배경을 흰색으로 통일하고 라벨의 별도 색상 면과 반복 잠금 설명을 제거한다. 기본정보도 라벨·입력 가로 정렬을 사용한다. 입력칸은 옅은 색상으로 구별하며 조회 상태의 텍스트는 비활성 대신 읽기 전용으로 표시하여 값의 대비를 유지한다. 수정 권한은 기존 presenter/application 정책을 유지한다.


### 메인 탐색 바 제거 (2026-09-28)

메인에서는 프로젝트 제목·Tests/장치/결과 탭·상태 배지의 탐색 바를 표시하지 않는다. Test 관리 본문과 추가·복제·삭제·수정·취소·저장·실험 열기 작업 버튼을 유지하고 같은 작업 줄에 설정 버튼을 제공한다. 설정은 별도 크기 조절 가능한 다이얼로그로 열며 장치/NMEA 파일 및 데모 실패 시나리오를 관리한다. 본문은 스크롤 가능하고 닫기 버튼은 고정이다. 설정을 닫거나 다시 열어도 선택한 시나리오와 Test 편집 초안을 유지한다. 실제 로봇 연결은 기존 주입형 어댑터를 사용하며 미확정된 장비 연결 정보를 입력하는 기능을 새로 만들지 않는다.
결과 탭은 메인에서 제거한다. 실험 창의 결과 보기 버튼은 별도 결과 다이얼로그를 열며 메인의 Test 관리 화면을 전환하지 않는다. 결과 저장과 복원은 유지한다.
