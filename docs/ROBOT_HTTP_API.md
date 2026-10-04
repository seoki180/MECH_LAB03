# 로봇 HTTP API 규격 (v1)

MECHLab 태블릿 앱(이하 **앱**)이 LAN으로 연결된 **로봇 컴퓨터**(이하 **로봇**)에
시험 설정을 올리고 시험을 시작시키는 규격이다.

이 문서는 **그대로 읽고 양쪽을 구현할 수 있는 수준**을 목표로 한다. 여기에 없는
필드·엔드포인트·동작은 규격이 아니다. 확정되지 않은 것은 "미확정"으로 적었고,
임의로 정한 값은 "제안 기본값"으로 표시했다. 제안 기본값은 실제 장비 요구사항이
아니므로 로봇 담당자와 맞춘 뒤 바꿀 수 있다.

**이 규격은 단방향 제어 경로다.** 앱은 설정을 올리고 시작 신호를 준 뒤 연결을 닫는다.
진행 상태 조회와 중지 요청은 **규격에 없다**(§8). 시작 이후 시험의 진행과 종료는
로봇이 스스로 관리한다. 연결 확인(`/health`)은 설정할 때 한 번만 보내며 시험 흐름에
들어 있지 않다(§1.1).

- 앱 쪽 구현: `src/experiment_app/infrastructure/http_robot.py`
- 로봇 쪽 참고 구현(표준 라이브러리만 사용): `tests/robot_stub.py`
- 규격 검증 테스트: `tests/test_http_robot.py`

---

## 1. 전체 흐름

요청은 두 시점에 나뉘어 나간다. **연결 확인은 설정할 때 한 번**, **시험 요청은
시작할 때**다.

### 1.1 설정할 때 (한 번)

사용자가 장치 설정에서 로봇 주소를 넣고 **로봇 연결 시험**을 누를 때만 보낸다.

```
[앱]                                             [로봇]
    GET  /api/v1/health                   ───▶   받을 준비가 됐는지 확인
```

시험을 시작할 때는 이 요청을 **보내지 않는다**. 로봇이 꺼져 있는지 미리 알고 싶을
때 쓰는 수단이며, 누르지 않아도 시험은 시작할 수 있다(그 경우 §1.2에서 실패로 드러난다).

### 1.2 실험 창을 열 때 (시험 세팅)

메인 창에서 **시험시작**을 눌러 실험 창이 열리는 시점이다. 아직 구동하지 않는다.

```
[앱]                                             [로봇]
 1  POST /api/v1/test/settings          ───▶   실험 입력 데이터 적용·검증
 2  POST /api/v1/test/target            ───▶   시험시나리오 target.csv 적재
    (시나리오가 없는 시험이면 2번을 보내지 않는다)
```

- 1·2번은 **세팅만** 한다. 이것을 받은 것만으로 로봇이 움직여서는 안 된다.
- 둘 중 하나라도 실패하면 앱은 **시작 버튼을 막고** 사유를 보여준다. 사용자는 실험
  창을 닫고 원인을 고친 뒤 다시 열어야 한다.
- 로봇이 꺼져 있으면 여기서 `ROBOT_UNREACHABLE`로 드러난다.

### 1.3 시작을 누를 때 (실제 구동)

실험 창에서 **시작**을 누르는 시점이다.

```
[앱]                                             [로봇]
 3  POST /api/v1/test/start             ───▶   실제 시험 시작
                                               (이후 앱은 요청을 보내지 않는다)
```

- 실제 구동은 이 요청으로만 시작한다.
- 응답을 받으면 앱은 연결을 닫는다. 그 뒤로 이 규격의 요청은 오지 않는다.

**세팅과 시작을 나눈 이유.** 시작 버튼을 누른 뒤에 설정을 보내면, 로봇이 설정을 받고
검증하는 시간만큼 구동이 늦어진다. 설정 오류도 시작을 누른 뒤에야 알게 된다. 미리
보내 두면 시작 신호 하나만으로 즉시 구동할 수 있고, 설정 문제는 누르기 전에 드러난다.

### 1.4 전체 순서

```
[설정 화면]  로봇 연결 시험  ──▶  GET  /health            (선택, 한 번)
[메인 창]    시험시작        ──▶  POST /test/settings
                                 POST /test/target
[실험 창]    시작            ──▶  POST /test/start
```

---

## 2. 전송 공통 규격

| 항목 | 값 |
| --- | --- |
| 프로토콜 | HTTP/1.1 평문 (랜선 직결 구간 전용, TLS 미사용) |
| 기본 주소 | `http://<로봇 IP>:8080` — 포트 8080은 **제안 기본값**, 앱 설정 화면(장치 설정 → 로봇 연결)에서 변경 가능 |
| 경로 접두사 | `/api/v1` |
| 문자 인코딩 | UTF-8 고정 (JSON·CSV 모두) |
| 요청 본문 | JSON(`application/json`) 또는 CSV(`text/csv`) — 엔드포인트별 지정 |
| 응답 본문 | 항상 JSON(`application/json; charset=utf-8`). 성공·오류 모두 본문이 있어야 한다 |
| 응답 시간 | 앱 타임아웃은 요청마다 3초 (§8) |
| 연결 | 요청마다 새 연결로 보내고 응답을 받으면 닫는다. keep-alive에 의존하지 않는다 |

### 2.1 공통 요청 헤더

`/health`를 제외한 모든 요청에 붙는다.

| 헤더 | 필수 | 설명 |
| --- | --- | --- |
| `X-Session-Id` | 필수 | 앱이 만든 실행 세션 식별자. 32자 소문자 16진수. 시작을 누를 때마다 새로 만든다 |
| `X-Sequence` | 필수 | 세션 안에서 1부터 증가하는 **명령 번호**(정수). `/settings`와 `/target`은 같은 `configure` 명령이라 **같은 번호**를 쓴다 |
| `X-Test-Id` | 필수 | 시험 식별자(문자열) |
| `X-Test-Revision` | 필수 | 시험 저장 버전(정수). 앱은 저장된 버전을 고정해 보낸다 |
| `Accept` | 권장 | `application/json` |

### 2.2 공통 응답 본문 (성공)

```json
{
  "ok": true,
  "session_id": "3f1c0e8a9b6d4f2a8c7e5b1d0a9f8e7c",
  "sequence": 2,
  "state": "ready",
  "message": "설정 적용 완료"
}
```

| 필드 | 형 | 필수 | 설명 |
| --- | --- | --- | --- |
| `ok` | bool | 필수 | 성공은 `true` |
| `session_id` | string | 필수 | 요청의 `X-Session-Id`를 **그대로** 되돌려준다 |
| `sequence` | int | 필수 | 요청의 `X-Sequence`를 **그대로** 되돌려준다 |
| `state` | string | 필수 | 응답 시점의 로봇 상태. §5 상태값 |
| `message` | string | 선택 | 사람이 읽는 설명. 앱 상태 표시줄에 그대로 보인다 |

> **중요** — 앱은 `session_id`·`sequence`가 요청과 다르면 응답을 믿지 않고
> `ROBOT_REPLY_MISMATCH` 오류로 시험을 중단한다. 지연 응답이 다음 명령의 성공으로
> 오인되는 것을 막기 위한 규칙이다. 반드시 받은 값을 그대로 echo 한다.

### 2.3 공통 응답 본문 (오류)

```json
{
  "ok": false,
  "session_id": "3f1c0e8a9b6d4f2a8c7e5b1d0a9f8e7c",
  "sequence": 2,
  "state": "error",
  "error": {
    "code": "SETTINGS_INVALID",
    "message": "point_angle.Zero[1] 값을 숫자로 읽을 수 없습니다.",
    "details": { "field": "point_angle.Zero[1]", "value": "abc" }
  }
}
```

| 필드 | 형 | 필수 | 설명 |
| --- | --- | --- | --- |
| `ok` | bool | 필수 | 오류는 `false` |
| `session_id`, `sequence` | | 필수 | 성공과 같은 규칙으로 echo. 세션을 식별할 수 없어 echo가 불가능하면 빈 문자열/`0`을 넣는다 |
| `state` | string | 필수 | §5 상태값 |
| `error.code` | string | 필수 | §7 오류 코드 표의 값. 표에 없는 코드는 앱이 `ROBOT_REJECTED`로 일괄 처리한다 |
| `error.message` | string | 필수 | **다음에 무엇을 하면 되는지 알 수 있는 한국어 문구.** 앱이 사용자에게 그대로 보여준다 |
| `error.details` | object | 선택 | 기계가 읽는 부가 정보. 필드명·허용 범위 등 |

HTTP 상태 코드와 `error.code`는 함께 보낸다. 앱은 **본문의 `error.code`를 기준으로**
판단하고 HTTP 상태 코드는 로그에만 쓴다. 본문이 JSON이 아니거나 `ok`가 없으면
앱은 `ROBOT_REPLY_INVALID`로 처리한다.

| HTTP | 쓰는 경우 |
| --- | --- |
| 200 | 성공 |
| 400 | 본문/헤더 형식 오류, 필수 필드 누락 |
| 409 | 상태 충돌 (세션 충돌, 번호 역행, 설정 없이 시작, 이미 실행 중) |
| 415 | `Content-Type`이 규격과 다름 |
| 422 | 형식은 맞지만 값이 유효하지 않음 (설정값 범위, CSV 내용) |
| 500 | 로봇 내부 오류 |
| 503 | 장치가 아직 준비되지 않음 / 다른 작업 중 |

---

## 3. 세션과 명령 번호 규칙

- `X-Session-Id`는 한 번의 시험 실행을 가리킨다. 앱이 시작을 누를 때 새로 만든다.
- 로봇은 **현재 세션 하나만** 유지한다. `/settings`를 받으면 그 세션을 현재 세션으로
  삼는다.
- 현재 세션이 **실행 중**(`running`)인데 다른 `X-Session-Id`로 요청이 오면
  `409 SESSION_CONFLICT`로 거절한다. 두 대의 태블릿이 같은 로봇을 동시에 잡는 것을
  막기 위한 규칙이다.
- `X-Sequence`는 같은 세션 안에서 **감소하지 않아야** 한다. 더 작은 번호가 오면
  `409 SEQUENCE_REGRESSED`로 거절한다(지연 도착한 옛 명령을 적용하지 않기 위해서다).
- `/settings`와 `/target`은 한 `configure` 명령의 두 요청이므로 같은 번호가 온다.
  같은 번호의 재전송은 허용한다.
- 로봇은 세션이 바뀌면 이전 세션의 설정·시나리오·상태를 버린다.

---

## 4. 값이 없음(`null`)의 취급

앱은 사용자가 입력하지 않은 값을 `null`로 보낸다. **0으로 바꿔 보내지 않는다.**
`0`은 "측정·설정된 0"이고 `null`은 "아직 없음"이라서 구별해야 한다.

로봇은 자기 구동에 필요한 값이 `null`이면 그 사실을 **거절로** 알린다
(`422 SETTINGS_INCOMPLETE`). 기본값으로 대체해 조용히 진행하지 않는다.
어떤 값이 필수인지는 로봇 쪽 요구사항이며 이 문서에서 정하지 않는다(미확정).

---

## 5. 로봇 상태값(`state`)

응답마다 실리는 현재 상태다. 앱은 이 값을 상태 표시에만 쓴다(조회용 엔드포인트는
없다 — §8).

| 값 | 뜻 |
| --- | --- |
| `idle` | 세팅 없음, 대기 |
| `ready` | 세팅 완료, 시작 대기 |
| `running` | 시험 구동 중 |
| `error` | 로봇 오류 |

표에 없는 문자열을 보내면 앱은 그 문자열을 그대로 상태 표시에 쓴다.

---

## 6. 엔드포인트

### 6.1 `GET /api/v1/health`

로봇이 요청을 받을 준비가 됐는지 확인한다. 본문·헤더 없이 호출한다.

**이 요청은 시험 흐름에 들어 있지 않다.** 앱은 설정 화면에서 사용자가 **로봇 연결
시험**을 누를 때만 보낸다(§1.1). 시험을 시작할 때는 `/test/settings`가 첫 요청이다.

**응답 200**

```json
{ "ok": true, "session_id": "", "sequence": 0, "state": "idle", "message": "robot ready" }
```

로봇이 물리적으로 구동 불가 상태면 `503` + `error.code = DEVICE_NOT_READY`로 답한다.
이때 앱은 설정 화면에 그 사유를 그대로 보여준다. 다만 이것이 시작을 막지는 않는다 —
사용자가 연결 시험을 누르지 않고 바로 시작할 수 있으므로, **구동 가능 여부는
`/test/settings`와 `/test/start`에서도 각각 판단해야 한다**(§6.2, §6.4).

---

### 6.2 `POST /api/v1/test/settings` — 시험 세팅값 보내기

`Content-Type: application/json; charset=utf-8`

**요청 본문** — 앱의 **실험 입력 데이터 전체**다. 최상위 키는 아래 네 개이며 네 개가
항상 모두 들어간다. 그 밖의 키는 보내지 않는다. 입력되지 않은 값은 키를 빼지 않고
`null`로 보낸다(§4).

```json
{
    "point_angle": {
        "Zero": [
            0,
            -19
        ],
        "Accel": [
            22,
            -8.3
        ],
        "Brake": [
            -9.5,
            -16.6
        ]
    },
    "calibration_data": [
        0.02894060757546641,
        -13.496798692559123
    ],
    "limit_point": {
        "Accel": -0.2,
        "Brake": -5
    },
    "zero_brake_angle": -14
}
```

| 경로 | 형 | 필수 | 설명 |
| --- | --- | --- | --- |
| `point_angle` | object | 필수 | 아래 세 키를 **모두** 가진다. 키 이름과 대소문자는 고정이다 |
| `point_angle.Zero` | array | 필수 | 길이 2의 배열 |
| `point_angle.Accel` | array | 필수 | 길이 2의 배열 |
| `point_angle.Brake` | array | 필수 | 길이 2의 배열 |
| `calibration_data` | array | 필수 | 길이 2의 배열. 순서가 의미를 가진다 |
| `limit_point` | object | 필수 | `Accel`·`Brake` 두 키를 **모두** 가진다. 키 이름과 대소문자는 고정이다 |
| `limit_point.Accel` | number \| null | 필수 | 단일 값(배열이 아니다) |
| `limit_point.Brake` | number \| null | 필수 | 단일 값(배열이 아니다) |
| `zero_brake_angle` | number \| null | 필수 | 단일 값 |
| 모든 수치 값 | number \| null | | 유한한 수 또는 `null`(§4). `NaN`·`Infinity`는 보내지 않는다 |

각 값의 의미(각도/위치의 어느 축인지, 어떤 보정에 쓰는지)는 로봇 쪽 정의를 따른다.
앱은 사용자가 입력한 값을 **순서 그대로** 전달하며 단위 변환이나 반올림을 하지 않는다.
`calibration_data`처럼 유효숫자가 긴 값도 자리수를 줄이지 않고 보낸다. 단위는 미확정이다.

`error.details.field`에는 경로 표기를 쓴다 — `point_angle.Accel[1]`,
`calibration_data[0]`, `limit_point.Brake`, `zero_brake_angle`.

규격에 없는 최상위 키가 오면 로봇은 무시하지 말고 `400 SETTINGS_INVALID`로 거절한다.
설정 일부가 조용히 빠진 채로 시험이 돌아가는 것을 막기 위한 규칙이다. 새 설정 묶음이
확정되면 최상위 키로 추가하고 이 문서를 같은 작업에서 갱신한다(§10).

**응답 200** — 로봇이 값을 실제로 **검증하고 적용한 뒤에만** 성공을 보낸다. 받기만 하고
성공을 답하면 앱이 잘못된 설정으로 시험을 시작한다.

```json
{ "ok": true, "session_id": "3f1c…", "sequence": 2, "state": "ready", "message": "설정 적용 완료" }
```

**주요 오류**

| HTTP | code | 상황 |
| --- | --- | --- |
| 400 | `BAD_REQUEST` | JSON 파싱 실패, 헤더 누락 |
| 400 | `SETTINGS_INVALID` | 최상위 키 누락·추가, 하위 키 부족, 배열 길이가 2가 아님, 숫자 아님 |
| 409 | `SESSION_CONFLICT` | 다른 세션이 실행 중 |
| 409 | `SEQUENCE_REGRESSED` | 명령 번호 역행 |
| 409 | `ALREADY_RUNNING` | 실행 중에는 설정을 바꾸지 않는다 |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | `Content-Type`이 JSON이 아님 |
| 422 | `SETTINGS_OUT_OF_RANGE` | 값이 장비 허용 범위를 벗어남 (범위는 로봇이 정한다) |
| 422 | `SETTINGS_INCOMPLETE` | 구동에 필요한 값이 `null` |

**curl 예**

```bash
curl -i -X POST http://192.0.2.10:8080/api/v1/test/settings \
  -H 'Content-Type: application/json; charset=utf-8' \
  -H 'X-Session-Id: 3f1c0e8a9b6d4f2a8c7e5b1d0a9f8e7c' \
  -H 'X-Sequence: 1' -H 'X-Test-Id: demo-0' -H 'X-Test-Revision: 2' \
  --data-binary @test.json
```

---

### 6.3 `POST /api/v1/test/target` — 시험시나리오 CSV 보내기

`Content-Type: text/csv; charset=utf-8`

**요청 본문** — `target.csv` 파일 내용을 **그대로** 담는다(multipart 아님, Base64 아님).
앱에 저장된 파일과 바이트 단위로 같은 내용이다.

```
time,target_v
0.0,0.0
0.1,0.35
0.2,0.71
```

| 규칙 | 값 |
| --- | --- |
| 첫 줄 | `time,target_v` 고정 (머리글 필수) |
| 열 구분 | 콤마 |
| 줄 끝 | CRLF(`\r\n`) |
| 인코딩 | UTF-8, BOM 없음 |
| `time` | 초 단위. 0 이상, 행마다 **증가**해야 한다 |
| `target_v` | 그 시각의 목표값. 유한한 수. 지수 표기(`4.82E-06`) 허용 |
| 행 수 | 1행 이상. 상한은 미확정 |

추가 헤더

| 헤더 | 필수 | 설명 |
| --- | --- | --- |
| `X-Filename` | 권장 | 항상 `target.csv` |
| `X-Row-Count` | 권장 | 머리글을 제외한 데이터 행 수. 로봇이 수신 누락을 검출하는 데 쓴다 |
| `Content-Length` | 필수 | 본문 바이트 수 |

**시나리오가 없는 시험**은 이 요청을 **보내지 않는다.** 빈 CSV나 머리글만 있는 CSV를
보내는 일은 없다. "목표값 없음"과 "점이 없는 곡선"을 구별하기 위한 규칙이다.
목표값 없이 구동할 수 없는 로봇은 `/start`에서 `409 NO_TARGET`으로 거절한다.

**응답 200**

```json
{ "ok": true, "session_id": "3f1c…", "sequence": 2, "state": "ready",
  "message": "target.csv 1200행 적재" }
```

**주요 오류**

| HTTP | code | 상황 |
| --- | --- | --- |
| 400 | `BAD_REQUEST` | 헤더 누락, 본문 없음 |
| 409 | `SETTINGS_REQUIRED` | `/settings`보다 먼저 왔다 (세팅 순서 위반) |
| 409 | `SESSION_CONFLICT` / `SEQUENCE_REGRESSED` / `ALREADY_RUNNING` | §3 |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | `Content-Type`이 `text/csv`가 아님 |
| 422 | `TARGET_INVALID` | 머리글 불일치, 숫자 아님, 시각이 증가하지 않음, 데이터 행 없음 |
| 422 | `TARGET_ROW_COUNT_MISMATCH` | `X-Row-Count`와 실제 행 수가 다름 |
| 413 | `TARGET_TOO_LARGE` | 로봇이 받을 수 있는 크기를 넘음 |

**curl 예**

```bash
curl -i -X POST http://192.0.2.10:8080/api/v1/test/target \
  -H 'Content-Type: text/csv; charset=utf-8' \
  -H 'X-Session-Id: 3f1c0e8a9b6d4f2a8c7e5b1d0a9f8e7c' \
  -H 'X-Sequence: 1' -H 'X-Test-Id: demo-0' -H 'X-Test-Revision: 2' \
  -H 'X-Filename: target.csv' -H 'X-Row-Count: 1200' \
  --data-binary @target.csv
```

---

### 6.4 `POST /api/v1/test/start` — 실제 시험 시작

`Content-Type: application/json; charset=utf-8`

**세팅과 이 요청 사이에는 시간 간격이 있다.** 세팅은 실험 창을 열 때 보내고, 이
요청은 사용자가 시작을 누를 때 보낸다(§1.2, §1.3). 그 사이가 얼마나 벌어질지는
사용자에게 달렸다 — 몇 초일 수도, 몇 분일 수도 있다. 로봇은 세팅을 그동안 유지해야
하며, 유지할 수 없게 되면(인터록 해제, 비상정지 등) 이 요청을 `DEVICE_NOT_READY`나
`SETTINGS_REQUIRED`로 거절한다. 조용히 옛 설정으로 구동하지 않는다.

**요청 본문** — 직전에 세팅한 것이 맞는지 확인하는 값만 담는다.

```json
{ "test_id": "demo-0", "test_revision": 2 }
```

| 필드 | 형 | 필수 | 설명 |
| --- | --- | --- | --- |
| `test_id` | string | 필수 | `X-Test-Id`와 같은 값 |
| `test_revision` | int | 필수 | `X-Test-Revision`과 같은 값 |

로봇은 이 값이 `/settings`에서 받은 값과 다르면 `409 TEST_MISMATCH`로 거절한다.
다른 시험의 설정으로 시작하는 것을 막기 위한 확인이다.

**응답 200** — 구동을 **실제로 시작한 뒤** 답한다. 이 응답이 앱이 받는 마지막
응답이며, 앱은 받고 나서 연결을 닫는다.

```json
{ "ok": true, "session_id": "3f1c…", "sequence": 3, "state": "running", "message": "시험 시작" }
```

**주요 오류**

| HTTP | code | 상황 |
| --- | --- | --- |
| 409 | `SETTINGS_REQUIRED` | `/settings`를 받지 못했다 |
| 409 | `NO_TARGET` | 목표값이 필요한데 `/target`을 받지 못했다 |
| 409 | `TEST_MISMATCH` | 본문의 시험/버전이 세팅과 다름 |
| 409 | `ALREADY_RUNNING` | 이미 실행 중 |
| 409 | `SESSION_CONFLICT` / `SEQUENCE_REGRESSED` | §3 |
| 503 | `DEVICE_NOT_READY` | 장치가 준비되지 않음(인터록, 비상정지 등) |
| 500 | `DEVICE_ERROR` | 시작 중 내부 오류 |

**curl 예**

```bash
curl -i -X POST http://192.0.2.10:8080/api/v1/test/start \
  -H 'Content-Type: application/json; charset=utf-8' \
  -H 'X-Session-Id: 3f1c0e8a9b6d4f2a8c7e5b1d0a9f8e7c' \
  -H 'X-Sequence: 2' -H 'X-Test-Id: demo-0' -H 'X-Test-Revision: 2' \
  -d '{"test_id":"demo-0","test_revision":2}'
```

---

## 7. 오류 코드 전체 표

로봇이 `error.code`로 보내는 값과, 앱이 사용자에게 보여주는 내부 코드의 대응이다.

| 로봇 `error.code` | 뜻 | 앱 내부 코드 |
| --- | --- | --- |
| `BAD_REQUEST` | 요청 형식·헤더 오류 | `ROBOT_REJECTED` |
| `UNSUPPORTED_MEDIA_TYPE` | `Content-Type` 불일치 | `ROBOT_REJECTED` |
| `SETTINGS_INVALID` | `/settings` 본문의 구조/형 오류 | `ROBOT_REJECTED` |
| `SETTINGS_OUT_OF_RANGE` | 값이 허용 범위 밖 | `ROBOT_REJECTED` |
| `SETTINGS_INCOMPLETE` | 필요한 값이 `null` | `ROBOT_REJECTED` |
| `SETTINGS_REQUIRED` | 세팅 전에 다음 단계가 왔다 | `ROBOT_REJECTED` |
| `TARGET_INVALID` | CSV 내용 오류 | `ROBOT_REJECTED` |
| `TARGET_ROW_COUNT_MISMATCH` | 행 수 불일치 | `ROBOT_REJECTED` |
| `TARGET_TOO_LARGE` | CSV가 너무 큼 | `ROBOT_REJECTED` |
| `NO_TARGET` | 목표값 없이 시작 요청 | `ROBOT_REJECTED` |
| `TEST_MISMATCH` | 시작 요청의 시험/버전 불일치 | `ROBOT_REJECTED` |
| `SESSION_CONFLICT` | 다른 세션이 점유 중 | `ROBOT_REJECTED` |
| `SEQUENCE_REGRESSED` | 명령 번호 역행 | `ROBOT_REJECTED` |
| `ALREADY_RUNNING` | 이미 실행 중 | `ROBOT_REJECTED` |
| `DEVICE_NOT_READY` | 장치 준비 안 됨 | `ROBOT_REJECTED` |
| `DEVICE_ERROR` | 로봇 내부 오류 | `ROBOT_REJECTED` |

앱이 스스로 만드는 코드(로봇이 보내는 값이 아니다):

| 앱 내부 코드 | 뜻 | 사용자에게 보이는 상황 |
| --- | --- | --- |
| `ROBOT_UNREACHABLE` | 연결 거부·주소 오류·DNS 실패 | 랜선/주소 확인 안내 |
| `ROBOT_TIMEOUT` | 응답 시간 초과 | 로봇 프로그램 상태 확인 안내 |
| `ROBOT_REPLY_INVALID` | 응답이 JSON이 아니거나 필수 필드 누락 | 규격 불일치 안내 |
| `ROBOT_REPLY_MISMATCH` | `session_id`/`sequence` echo 불일치 | 규격 불일치 안내 |
| `ROBOT_PAYLOAD_INVALID` | 앱이 보낼 설정을 만들 수 없음(시험 자료 문제) | 시험 값 확인 안내 |
| `ROBOT_CANCELLED` | 사용자가 시작 중 취소 | 중지로 처리 |

---

## 8. 규격에 없는 것 — 상태 조회와 중지

**진행 상태 조회(`GET /status`)와 시험 중지(`POST /stop`)는 이 규격에 없다.** 요청하지
않기로 결정한 것이며, 로봇이 구현할 필요도 없다. 그래서 다음이 따라온다. 양쪽이 같이
알고 있어야 한다.

| 결과 | 내용 |
| --- | --- |
| 시작 이후 로봇 상태를 모른다 | 앱 화면의 로봇 상태는 `/start` 응답의 `state`에서 멈춘다. 그 뒤 로봇이 오류로 멈춰도 앱은 알 수 없다 |
| 앱이 로봇을 멈출 수 없다 | 앱에서 실험을 중지하거나 창을 닫아도 **로봇은 계속 구동한다.** 중지는 로봇 자체 조작(조작반·비상정지)으로 한다 |
| 수신 지연 감지가 없다 | 상태 갱신을 근거로 한 `ROBOT_STALE` 판정이 없다. 랜선이 빠져도 앱은 시작이 성공한 것으로 남는다 |
| 앱 기록에 로봇 진행이 남지 않는다 | 기록 파일에는 보낸 설정과 시작 성공 여부만 남고, 로봇 쪽 진행 상태는 남지 않는다 |

**안전 관련 주의** — 앱은 중지 신호를 보내지 않는다. 시작한 시험을 멈추는 수단은
로봇 쪽에만 있어야 한다. 앱을 닫으면 로봇이 멈춘다고 가정하고 운용하면 안 된다.

둘 중 하나라도 필요해지면 §1의 흐름에 5·6번을 되살리고, 상태 조회에는 갱신마다
증가하는 `status_sequence`(수신 생존 신호)를, 중지에는 "실행 중이 아닐 때도 성공"
규칙을 함께 정해야 한다.

---

## 9. 시간 제한과 재시도

| 요청 | 앱 타임아웃 | 재시도 |
| --- | --- | --- |
| `GET /health` (설정 화면의 연결 시험) | 3초 | 없음 |
| `POST /settings`, `/target`, `/start` | 각 3초 | **없음.** 같은 명령을 자동으로 다시 보내지 않는다 |

POST를 자동 재시도하지 않는 이유는 `/start`가 중복 실행될 위험이 있기 때문이다.
실패는 사용자에게 알리고 사용자가 다시 시작하게 한다. 로봇은 같은
`(session_id, sequence)` 요청을 다시 받으면 멱등하게 처리한다.

---

## 10. 구현 범위와 미확정 항목

**이 규격에 포함되지 않은 것**

- 진행 상태 조회와 시험 중지: §8 참고.
- `ar_trapezoidal_step` / `pf_straight_line` 등 로봇 입력 설정 묶음: 앱 화면에는 있으나
  전송 규격이 미확정이라 `/settings`에 넣지 않았다. 확정되면 `/settings` 본문에 최상위
  키로 추가하고 이 문서를 같은 작업에서 갱신한다.
- 인증·암호화: 랜선 직결 구간 전용이므로 없다. 외부망에 노출하면 안 된다.
- 로봇 → 앱 측정 데이터 스트림: 이 규격은 제어 경로만 다룬다. 측정값 수신은
  HI-EDGE WebSocket 경로(`infrastructure/hiedge.py`)가 담당한다.

**미확정 항목(로봇 담당자와 확정 필요)**

- 포트 번호(제안 8080)와 로봇 IP
- `point_angle` 두 원소의 의미와 단위, 허용 범위
- `calibration_data` 두 원소의 의미와 단위, 허용 범위
- `limit_point.Accel` / `limit_point.Brake`의 의미와 단위, 허용 범위
- `zero_brake_angle`의 단위와 허용 범위
- 어떤 값이 구동 필수인지(`SETTINGS_INCOMPLETE` 판단 기준)
- `target.csv` 최대 행 수 / 최대 바이트
- 시작한 시험을 멈추는 절차(로봇 쪽 조작 수단)

---

## 11. 앱에서 로봇 주소를 지정하는 방법

우선순위는 **명령줄 > 저장된 설정 > 데모**다. 어디에도 주소가 없으면 데모 로봇으로
동작한다. 없는 주소로 접속을 시도하지 않는다 — 사용자에게는 원인 없는 시작 실패로
보이기 때문이다.

**1) 설정 화면 (운용 시 기본 경로)**

메인 창의 장치 설정 → **로봇 연결 (HTTP)** 에서 지정한다. 센서·GPS를 받는
HI-EDGE LAN 설정과 **같은 화면의 다른 항목**이다(서로 다른 기기이므로 주소·포트를
공유하지 않는다).

| 조작 | 동작 |
| --- | --- |
| `실제 로봇으로 전송` 체크 해제 | 데모 로봇. 주소는 남지만 실제로 보내지 않는다 |
| 로봇 주소 / 포트 입력 후 `로봇 주소 적용` | 즉시 반영하고 다음 실행까지 저장한다 |
| `로봇 연결 시험` | 입력된 주소로 `/health`를 한 번 보내 결과를 보여준다 |

연결 시험은 **입력된 주소**로 한다(적용 전에도 확인할 수 있다). 시험 흐름에는
`/health`가 없으므로, 로봇이 꺼져 있는지 시작 전에 아는 수단은 이 버튼뿐이다.
누르지 않아도 시험은 시작할 수 있고, 그 경우 `/settings` 단계에서
`ROBOT_UNREACHABLE`로 드러난다.

체크만으로는 적용되지 않는다. 주소를 고치는 중에 체크가 바로 적용되면 빈 주소나
옛 주소로 전송이 만들어지기 때문이다. 주소를 비운 채 켜는 것은 거부한다.
실험 창이 열려 있는 동안에는 바꿀 수 없다(진행 중인 시험이 어느 로봇으로 갔는지
알 수 없게 되면 안 된다).

**2) 저장 파일** — `<data_dir>/settings.json` (기본값은 실행 파일 옆 `.mechlab`)

```json
{
  "robot": { "host": "192.168.0.50", "port": 8080, "enabled": true }
}
```

`enabled`가 `false`면 주소를 무시하고 데모로 동작한다. 손으로 고쳐도 되며, 값이
깨져 있으면 그 항목만 기본값으로 돌린다(앱 시작을 막지 않는다). `lan` 항목과 서로
영향을 주지 않는다.

**3) 명령줄** — 현장 점검과 시험용

```bash
MECHLab.exe --robot-url http://192.168.0.50:8080
```

이 값은 저장된 설정보다 우선하고, 설정 화면에도 같은 주소가 표시된다.

**연결 확인** — 주소를 적용한 뒤 `curl`로 먼저 확인하는 것이 가장 빠르다.

```bash
curl http://192.168.0.50:8080/api/v1/health
```

---

## 12. 참고 서버로 시험하기

실제 로봇 없이 확인할 때는 `tests/robot_stub.py`를 로봇 대신 띄운다. 표준
라이브러리만 쓰므로 로봇 컴퓨터에 이 파일 하나만 복사하면 설치 없이 돈다.

```bash
python tests/robot_stub.py --port 8080
```

받은 요청과 돌려준 응답을 그 자리에서 찍는다. 설정 화면에서 '로봇 연결 시험'을
누르고, 메인 창에서 시험시작(실험 창 열기), 실험 창에서 시작을 차례로 누르면
이렇게 보인다.

```
로봇 참고 서버 http://0.0.0.0:8080/api/v1 (Ctrl+C로 종료)
요청을 기다립니다. 앱에서 시작을 누르면 아래에 받은 값이 찍힙니다.
[OK  ] GET /api/v1/health -> 200 robot ready
[OK  ] POST /api/v1/test/settings -> 200 설정 적용 완료
       받은 값: point_angle Zero=[0, -19], Accel=[22, -8.3], Brake=[-9.5, -16.6]
       받은 값: calibration_data=[0.02894060757546641, -13.496798692559123]
       받은 값: limit_point Accel=-0.2, Brake=-5 · zero_brake_angle=-14
[OK  ] POST /api/v1/test/target -> 200 target.csv 301행 적재
       받은 값: target.csv 301행 (첫 (0.0, 0.0), 끝 (30.0, 10.0))
[OK  ] POST /api/v1/test/start -> 200 시험 시작
       받은 값: 시험 demo-0 rev 1 · 구동 상태 running
```

앞의 세 줄과 마지막 줄 사이에 **시간 간격이 보인다** — 설정 두 개는 실험 창을 열 때
나가고, `/start`는 사용자가 시작을 누를 때 나간다. 연결 시험을 누르지 않았다면 첫
줄의 `/health`는 찍히지 않는다.

응답 코드만 찍지 않고 **받은 값까지** 보여주는 이유는, 200이 떴는데 값이 제대로
전달됐는지는 따로 확인해야 하기 때문이다. 거절한 경우에는 오류 코드를 그대로 찍는다.

```
[FAIL] POST /api/v1/test/settings -> 422 SETTINGS_OUT_OF_RANGE: Accel 각도가 ...
```

| 옵션 | 용도 |
| --- | --- |
| `--fail settings\|target\|start\|health` | 해당 단계를 거절해 앱의 오류 경로를 확인한다 |
| `--requires-target` | 시나리오 없는 시험의 시작을 거절한다 |
| `--quiet` | 받은 요청을 찍지 않는다 |

**주의** — 참고 서버는 `/start`를 받으면 `running` 상태로 남는다. 규격에 중지가
없으므로 앱이 되돌릴 수 없고, 다음 시험은 `SESSION_CONFLICT`로 거절된다. 연속으로
시험하려면 서버를 재시작한다. 실제 로봇도 같은 제약을 받는다(§8).
