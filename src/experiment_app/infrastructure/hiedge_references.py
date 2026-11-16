"""HI-EDGE 기준점(reference) HTTP 클라이언트.

``/ws/data`` 는 **장치에 등록된 기준점마다** ``position_offsets`` 항목을 하나씩 실어
보낸다. 실험을 시작한 위치 기준의 변위를 받으려면 그 위치를 기준점으로 등록하고,
돌아오는 항목 중 **우리 ``reference_id``** 를 골라 읽어야 한다.

왜 이 모듈이 따로 있나
----------------------
측정값 수신은 WebSocket(``hiedge.py``)이고 기준점 등록·삭제는 HTTP다. 포트도 같고
TLS도 같지만 수명이 다르다 — 소켓은 실험 내내 열려 있고, 등록·삭제는 시작·종료
순간에 한 번씩 오가는 짧은 요청이다. 섞어 두면 수신 루프의 재접속 처리와 요청
타임아웃이 한 함수에 엉킨다.

장치 쪽 제약(규격 문서 기준, 실측으로 확인함)
---------------------------------------------
- 기준점은 **장치 전역 공유 상태**다. 우리가 등록하면 다른 구독자의 목록에도 보이고,
  전원을 껐다 켜도 남는다. 그래서 실험이 끝나면 반드시 지운다.
- 최대 16개다. 장치에 이미 다른 사람이 등록해 둔 것이 있으면 그만큼 자리가 줄어든다.
- ``mode:"current"`` 등록은 **유효한 위치와 정차 상태**(0.5 km/h 이하)를 요구한다.
  실내처럼 fix가 없으면 등록 자체가 거절된다.
- 쓰기(POST/DELETE)에는 ``Origin`` 헤더가 필요하며 Host와 정확히 같아야 한다.
  읽기(GET)와 WebSocket은 세션 쿠키도 Origin도 필요 없다.
- 등록 응답은 **새 기준점 하나가 아니라 목록 전체**를 돌려준다. 그래서 우리가 만든
  것을 이름으로 찾아낸다. 이름이 겹치면 남의 것을 우리 것으로 착각할 수 있으므로
  이름에 난수를 붙인다.
"""

import json
import urllib.error
import urllib.request
import uuid

from experiment_app.domain.test_definition import AppError

PATH = "/api/stream/references"

# 우리가 만든 기준점을 알아보는 이름 규칙. 비정상 종료로 남은 것을 다음 실행에서
# 지울 때 이 접두사로만 고른다 — 장치 담당자가 등록한 기준점은 건드리지 않는다.
PREFIX = "MECHLab"

# 요청 하나의 상한. 로봇 HTTP 규격(3초)과 같은 값을 쓴다. 시작 버튼을 누른 뒤
# 기다리는 시간이므로 길게 잡지 않는다.
TIMEOUT = 3.0

# mode:"current" 등록이 허용되는 최대 속도. 장치가 거절하는 기준값이며, 미리
# 알려주기 위해 preflight에서도 같은 값을 쓴다.
MAX_REGISTER_SPEED_KMH = 0.5

# 평균 수집이 진행 중이면 즉시 등록도 202로 접수될 수 있다. 그때 완료를 기다리는 상한.
CAPTURE_WAIT = 5.0


def new_name():
    """이번 실행에서 쓸 기준점 이름. 접두사로 우리 것임을 밝힌다."""
    return f"{PREFIX}-{uuid.uuid4().hex[:8]}"


def is_ours(reference):
    return str(reference.get("name") or "").startswith(PREFIX)


class ReferenceClient:
    """기준점 목록 조회·등록·삭제. 실패는 모두 AppError로 올린다."""

    def __init__(self, host, port, tls, timeout=TIMEOUT):
        self.base = f"https://{host}:{port}"
        self.timeout = timeout
        # 프록시를 타면 링크로컬 주소에 닿지 못한다. 쿠키는 쓰지 않는다(세션 불필요).
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=tls))

    # --- 요청 ---

    def _call(self, method, path, body=None):
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if method != "GET":
            # 쓰기에는 Host와 정확히 같은 Origin이 필요하다.
            headers["Origin"] = self.base
        request = urllib.request.Request(self.base + path, data=data, method=method,
                                         headers=headers)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as error:
            raise AppError("LAN_REF_REJECTED", self._reason(error)) from error
        except urllib.error.URLError as error:
            raise AppError("LAN_REF_UNAVAILABLE",
                           f"기준점 API에 연결하지 못했습니다: {self.base} ({error.reason})") from error
        except OSError as error:
            raise AppError("LAN_REF_UNAVAILABLE",
                           f"기준점 API 요청 실패: {self.base} ({error})") from error
        if not payload:
            return {}
        try:
            return json.loads(payload)
        except json.JSONDecodeError as error:
            raise AppError("LAN_REF_INVALID", f"기준점 응답을 읽을 수 없습니다: {error}") from error

    def _reason(self, error):
        """장치가 돌려준 사유를 그대로 보여준다. 우리가 바꿔 쓰지 않는다."""
        detail = ""
        try:
            body = json.loads(error.read() or b"{}")
            detail = body.get("error") or body.get("detail") or ""
        except (ValueError, OSError):
            detail = ""
        if error.code == 409 and not detail:
            detail = "장치가 현재 상태에서 기준점 등록을 받지 않습니다."
        return f"기준점 요청 거절 (HTTP {error.code}){': ' + str(detail) if detail else ''}"

    # --- 조회 ---

    def listing(self):
        """목록 전체. ``references`` / ``max_references`` / ``capture`` 를 담는다."""
        return self._call("GET", PATH)

    def references(self):
        listing = self.listing()
        found = listing.get("references")
        return list(found) if isinstance(found, list) else []

    def free_slots(self):
        """우리 것을 지운 뒤 남는 자리 수. 등록 전에 자리를 확인하는 데 쓴다."""
        listing = self.listing()
        found = listing.get("references") or []
        limit = listing.get("max_references") or 16
        others = [entry for entry in found if not is_ours(entry)]
        return limit - len(others)

    # --- 등록 ---

    def register_current(self, name=None):
        """현재 위치를 기준점으로 등록하고 만들어진 항목을 돌려준다.

        응답은 목록 전체이므로 이름으로 찾는다. 평균 수집이 진행 중이어서 202로
        접수된 경우에는 목록을 다시 읽어 완료를 기다린다.
        """
        name = name or new_name()
        listing = self._call("POST", PATH, {"mode": "current", "name": name})
        entry = self._find(listing, name)
        if entry is not None:
            return entry
        entry = self._wait_for(name)
        if entry is None:
            raise AppError("LAN_REF_REJECTED",
                           f"기준점 '{name}'이 등록되지 않았습니다. 장치의 기준점 상태를 확인하세요.")
        return entry

    def _find(self, listing, name):
        for entry in listing.get("references") or ():
            if isinstance(entry, dict) and entry.get("name") == name:
                return entry
        return None

    def _wait_for(self, name):
        import time
        deadline = time.monotonic() + CAPTURE_WAIT
        while time.monotonic() < deadline:
            listing = self.listing()
            entry = self._find(listing, name)
            if entry is not None:
                return entry
            capture = listing.get("capture")
            if isinstance(capture, dict) and capture.get("state") in {"failed", "cancelled"}:
                raise AppError("LAN_REF_REJECTED",
                               f"기준점 수집이 {capture.get('state')}로 끝났습니다: {capture.get('reason') or '사유 없음'}")
            time.sleep(0.3)
        return None

    # --- 삭제 ---

    def delete(self, reference_id):
        self._call("DELETE", f"{PATH}/{reference_id}")

    def exists(self, reference_id):
        """그 기준점이 아직 장치에 있는가. 재시작할 때 이어 쓸 수 있는지 확인한다."""
        return any(entry.get("reference_id") == reference_id for entry in self.references())

    def purge(self):
        """지난 실행이 남긴 우리 기준점을 지운다. 지운 개수를 돌려준다.

        이름 접두사가 맞는 것만 고른다. 장치 담당자가 등록한 기준점은 우리가 지울
        대상이 아니다 — 다른 구독자가 그 변위를 보고 있을 수 있다.
        """
        removed = 0
        for entry in self.references():
            if not is_ours(entry):
                continue
            self.delete(entry["reference_id"])
            removed += 1
        return removed
