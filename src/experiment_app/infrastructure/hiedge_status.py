"""HI-EDGE 장치 상태 조회(``GET /api/latest``).

왜 WebSocket이 아니라 이 경로인가
---------------------------------
실험 창을 열어 둔 채 GPS fix를 기다리는 동안, 시작 버튼을 켤 수 있는지 **반복해서**
확인해야 한다. 그때마다 ``/ws/data`` 에 붙으면 장치의 구독 슬롯(전체 8개, FIFO)을
하나씩 썼다 떼게 되고, 슬롯이 차 있으면 **확인할 때마다 다른 구독자를 4001로 밀어낸다.**
``/api/latest`` 는 평범한 HTTP GET이라 그런 부작용이 없다.

이 경로는 ``edge-map.v1`` 규약이며 스트림(``hi-edge.speed-stream.v1``)과 다른
스키마다. 측정값을 여기서 읽지 않는다 — 측정의 SoT는 WebSocket이고, 이 모듈은
**시작 전 가부 판단에만** 쓴다.

세션 쿠키
---------
스트림·기준점 API와 달리 ``/api/latest`` 는 ``edgeMapSession`` 쿠키를 요구한다.
신뢰된 LAN에서 ``GET /`` 를 한 번 열면 발급된다. 401이면 다시 발급받고 한 번만
재시도한다.
"""

import json
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

from experiment_app.domain.test_definition import AppError

PATH = "/api/latest"
TIMEOUT = 3.0


class StatusClient:
    """장치의 현재 위치해·움직임 상태를 읽는다. 실패는 AppError로 올린다."""

    def __init__(self, host, port, tls, timeout=TIMEOUT):
        self.base = f"https://{host}:{port}"
        self.timeout = timeout
        self._jar = CookieJar()
        # 프록시를 타면 링크로컬 주소에 닿지 못한다.
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=tls),
            urllib.request.HTTPCookieProcessor(self._jar))
        self._has_session = False

    def _open_session(self):
        """LAN 세션 쿠키를 받는다. 받은 쿠키는 opener의 CookieJar에 남는다."""
        try:
            self._opener.open(self.base + "/", timeout=self.timeout).close()
        except (urllib.error.URLError, OSError) as error:
            raise AppError("LAN_STATUS_UNAVAILABLE",
                           f"장치 상태를 조회할 수 없습니다: {self.base} ({error})") from error
        self._has_session = True

    def latest(self):
        """``/api/latest`` 응답. 세션이 없거나 만료됐으면 발급받고 한 번 재시도한다."""
        if not self._has_session:
            self._open_session()
        try:
            return self._get()
        except AppError as error:
            if error.code != "LAN_STATUS_SESSION":
                raise
        self._has_session = False
        self._open_session()
        return self._get()

    def _get(self):
        request = urllib.request.Request(self.base + PATH, method="GET",
                                         headers={"Accept": "application/json"})
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as error:
            if error.code == 401:
                raise AppError("LAN_STATUS_SESSION", "장치 세션이 만료됐습니다.") from error
            raise AppError("LAN_STATUS_UNAVAILABLE",
                           f"장치 상태 조회 거절 (HTTP {error.code})") from error
        except (urllib.error.URLError, OSError) as error:
            raise AppError("LAN_STATUS_UNAVAILABLE",
                           f"장치 상태를 조회할 수 없습니다: {self.base} ({error})") from error
        try:
            return json.loads(payload)
        except json.JSONDecodeError as error:
            raise AppError("LAN_STATUS_INVALID", f"장치 상태 응답을 읽을 수 없습니다: {error}") from error


def fix_state(latest):
    """(위치 유효, 사유)를 돌려준다. 사유는 무효일 때만 채운다.

    ``solution_valid`` 와 ``stale`` 을 함께 본다. 만료된 해를 현재 해로 쓰지 않는다.
    """
    status = latest.get("status") or {}
    fix = status.get("fix") or {}
    if fix.get("solution_valid") is True and fix.get("stale") is not True:
        return True, ""
    if fix.get("stale") is True:
        return False, f"위치해 만료 · {fix.get('stale_reason') or '사유 없음'}"
    return False, (f"{fix.get('solution_label') or fix.get('fix_state') or 'fix 없음'}"
                   f" · 위성 {fix.get('satellites_used')}개")


def speed_kmh(latest):
    """현재 속도(km/h). 유효하지 않으면 None — 0으로 바꾸지 않는다."""
    status = latest.get("status") or {}
    motion = status.get("motion") or {}
    if motion.get("valid") is True and motion.get("stale") is not True:
        value = motion.get("speed_kmh")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    fix = status.get("fix") or {}
    value = fix.get("speed_kmh")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def snapshot(latest):
    """시작 전 화면에 보여 줄 장치 상태. **사실만** 담고 문구는 만들지 않는다.

    측정값이 아니다 — 시작을 누르기 전에는 WebSocket을 열지 않으므로 기록도 궤적도
    없다. 이 값은 "지금 장치가 위치를 잡고 있는가"를 보여 주기 위한 것이다.
    """
    status = latest.get("status") or {}
    fix = status.get("fix") or {}
    valid, reason = fix_state(latest)
    return {"valid": valid,
            "reason": reason,
            "label": fix.get("solution_label") or fix.get("fix_state"),
            "satellites": fix.get("satellites_used"),
            "latitude": fix.get("latitude") if valid else None,
            "longitude": fix.get("longitude") if valid else None,
            "speed_kmh": speed_kmh(latest)}
