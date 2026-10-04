# 로봇 측 HTTP 서버 구현 규약 (v1)

이 문서는 **태블릿 앱(클라이언트)이 보내는 요청의 양식**과 **로봇 컴퓨터(서버)가
돌려줘야 하는 응답·오류값**을 규정한다. 로봇 측은 기존 구동 코드 위에 이 규약을
만족하는 얇은 HTTP 서버만 올리면 된다.

- 앱이 보내는 것: 이 문서에 적힌 요청 4종. 그 외의 요청은 보내지 않는다.
- 서버가 지켜야 하는 것: 요청 양식 검증, 응답 양식, 오류값, 세션 규칙, 상태 관리.
- 이 문서에 없는 엔드포인트·필드·동작은 규약이 아니다. 만들지 않아도 되고, 만들어도
  앱은 쓰지 않는다.
- **단방향 제어 경로다.** 앱은 설정을 올리고 시작 신호를 준 뒤 연결을 닫는다.
  상태 조회와 중지 요청은 규약에 없다(§8).

확정되지 않은 값은 "미확정", 앱이 임의로 정한 값은 "제안 기본값"으로 적었다. 제안
기본값은 장비 요구사항이 아니므로 §9에서 합의해 바꿀 수 있다.

---

## 1. 요청 순서

요청은 **세 시점**에 나뉘어 온다. 한 번에 몰려 오지 않는다는 점이 중요하다.

**(1) 앱 설정 화면에서 로봇 주소를 넣고 `로봇 연결 시험`을 누를 때 — 선택, 1회**

```
[앱]                                          [로봇 서버]
    GET  /api/v1/health             ───▶   받을 준비가 됐는지 확인
```

사용자가 누르지 않으면 오지 않는다. 시험 시작 흐름에 들어 있지 않다.

**(2) 메인 창에서 `시험시작`을 눌러 실험 창이 열릴 때 — 세팅만, 구동 없음**

```
 1  POST /api/v1/test/settings      ───▶   실험 입력 데이터 검증 후 적용
 2  POST /api/v1/test/target        ───▶   target.csv 적재
     (시나리오가 없는 시험이면 2번은 오지 않는다)
```

둘 중 하나라도 실패하면 앱은 시작 버튼을 막는다. 사용자는 실험 창을 닫고 원인을
고친 뒤 다시 연다.

**(3) 실험 창에서 `시작`을 누를 때 — 실제 구동**

```
 3  POST /api/v1/test/start         ───▶   실제 구동 시작
                                           (이후 앱 요청은 없다)
```

세팅과 시작을 나눈 이유: 시작을 누른 뒤에 설정을 보내면 검증 시간만큼 구동이 늦어지고,
설정 오류도 누른 뒤에야 드러난다. 미리 보내 두면 시작 신호 하나로 즉시 구동할 수 있다.
**그래서 (2)와 (3) 사이에는 사용자가 조작하는 만큼의 시간 간격이 있다.** 세팅을 받고
몇 분 뒤에 `/start`가 올 수 있으므로 세팅 상태를 짧은 타임아웃으로 버리지 않는다.

규약의 핵심 세 가지

1. **(2)는 세팅만 한다.** 이 요청을 받은 것만으로 장비가 움직여서는 안 된다.
2. **`/settings`는 값을 실제로 적용한 뒤에만 성공을 답한다.** 받아두고 성공을 답하면
   앱이 잘못된 설정으로 시험을 시작한다.
3. **`/start`는 구동이 시작된 뒤 답하되, 시험 완료를 기다리지 않는다.** 앱 타임아웃은
   3초다. 구동 루프는 별도 스레드/프로세스로 돌리고 "시작됨" 시점에 응답한다.

---

## 2. 전송 공통 규약

| 항목 | 값 |
| --- | --- |
| 프로토콜 | HTTP/1.1 평문 (랜선 직결 구간 전용, TLS 미사용) |
| 주소 | `http://<로봇 IP>:8080` — 포트 8080은 **제안 기본값**, 설정으로 변경 가능 |
| 경로 접두사 | `/api/v1` |
| 인코딩 | UTF-8 고정 (JSON·CSV 모두) |
| 요청 본문 | JSON(`application/json`) 또는 CSV(`text/csv`) — 엔드포인트별 지정 |
| 응답 본문 | **항상** JSON. `Content-Type: application/json; charset=utf-8`. 성공·오류 모두 본문이 있어야 한다 |
| 응답 시간 | 앱 타임아웃은 요청마다 3초 |
| 연결 | 앱은 요청마다 새 연결을 쓰고 응답을 받으면 닫는다. keep-alive에 의존하지 않는다 |
| 인증 | 없다. 외부망에 노출되는 인터페이스에 바인드하지 않는다 |

### 2.1 공통 요청 헤더

`/health`를 제외한 모든 요청에 붙는다. 하나라도 없으면 `400 BAD_REQUEST`다.

| 헤더 | 필수 | 설명 |
| --- | --- | --- |
| `X-Session-Id` | 필수 | 실행 세션 식별자. 32자 소문자 16진수. 앱이 시작을 누를 때마다 새로 만든다 |
| `X-Sequence` | 필수 | 세션 안에서 1부터 증가하는 **명령 번호**(정수). `/settings`와 `/target`은 같은 `configure` 명령이라 **같은 번호**가 온다 |
| `X-Test-Id` | 필수 | 시험 식별자(문자열) |
| `X-Test-Revision` | 필수 | 시험 저장 버전(정수) |
| `Accept` | 선택 | `application/json` |

---

## 3. 응답 양식

### 3.1 성공 응답 (HTTP 200)

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
| `session_id` | string | 필수 | 요청의 `X-Session-Id`를 **그대로** echo |
| `sequence` | int | 필수 | 요청의 `X-Sequence`를 **그대로** echo |
| `state` | string | 필수 | 응답 시점의 로봇 상태(§5) |
| `message` | string | 선택 | 사람이 읽는 설명. 앱 상태 표시줄에 그대로 보인다 |

> **echo 규칙은 필수다.** `session_id`·`sequence`가 요청과 다르면 앱은 응답을 믿지
> 않고 시험을 중단한다(앱 오류 `ROBOT_REPLY_MISMATCH`). 지연 응답이 다음 명령의
> 성공으로 오인되는 것을 막기 위한 규칙이다. 값을 가공·정규화하지 말고 받은 문자열을
> 그대로 돌려준다.

### 3.2 오류 응답

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
| `session_id`, `sequence` | | 필수 | 성공과 같은 echo 규칙. 세션을 식별할 수 없어 echo가 불가능하면 `""` / `0` |
| `state` | string | 필수 | §5 상태값 |
| `error.code` | string | 필수 | §7 표의 값 |
| `error.message` | string | 필수 | **다음에 무엇을 하면 되는지 알 수 있는 한국어 문구.** 앱이 사용자에게 그대로 보여준다 |
| `error.details` | object | 선택 | 기계가 읽는 부가 정보(필드명, 허용 범위, 행 번호 등) |

규칙

- 앱은 **본문의 `error.code`를 기준으로** 판단하고 HTTP 상태 코드는 로그에만 쓴다.
  그래도 둘을 §7 표대로 함께 보낸다.
- 본문이 JSON이 아니거나 `ok`가 없으면 앱은 `ROBOT_REPLY_INVALID`로 처리한다.
  빈 본문 응답을 만들지 않는다.
- `error.message`에 `"error"`, `"invalid"` 같은 단어만 넣지 않는다. 사용자가 조치할 수
  있는 문장을 넣는다. (좋은 예: `"Accel 각도가 허용 범위(-30~30도)를 벗어났습니다."`)

### 3.3 HTTP 상태 코드 사용

| HTTP | 쓰는 경우 |
| --- | --- |
| 200 | 성공 |
| 400 | 본문/헤더 형식 오류, 필수 필드 누락 |
| 409 | 상태 충돌 (세션 충돌, 번호 역행, 세팅 없이 시작, 이미 실행 중) |
| 413 | 본문이 수용 한도를 넘음 |
| 415 | `Content-Type`이 규약과 다름 |
| 422 | 형식은 맞지만 값이 유효하지 않음 |
| 500 | 로봇 내부 오류 |
| 503 | 장치가 아직 준비되지 않음 / 다른 작업 중 |

---

## 4. 요청 양식 (엔드포인트별)

### 4.1 `GET /api/v1/health`

연결·장비 준비 확인. 본문도 공통 헤더도 없이 온다. 사용자가 앱 설정 화면에서
`로봇 연결 시험`을 누를 때만 오며, 시험 시작 흐름에는 들어 있지 않다(§1).

**성공 응답 200**

```json
{ "ok": true, "session_id": "", "sequence": 0, "state": "idle", "message": "robot ready" }
```

장비가 물리적으로 구동 불가 상태(인터록, 비상정지 등)면
`503` + `DEVICE_NOT_READY`로 답한다. 이때 앱은 시험을 시작하지 않는다.

---

### 4.2 `POST /api/v1/test/settings` — 시험 세팅값

`Content-Type: application/json; charset=utf-8`

**요청 본문** — 앱의 **실험 입력 데이터 전체**다. 최상위 키는 아래 네 개이며, 네 개가
**항상 모두** 온다. 사용자가 입력하지 않은 값은 생략되지 않고 `null`로 온다(§6).

```json
{
  "point_angle": {
    "Zero":  [0, -19],
    "Accel": [22, -8.3],
    "Brake": [-9.5, -16.6]
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
| `point_angle` | object | 필수 | 아래 세 키를 **모두** 가진다. 키 이름과 대소문자는 고정 |
| `point_angle.Zero` | array | 필수 | 길이 2 |
| `point_angle.Accel` | array | 필수 | 길이 2 |
| `point_angle.Brake` | array | 필수 | 길이 2 |
| `calibration_data` | array | 필수 | 길이 2. 순서가 의미를 가진다 |
| `limit_point` | object | 필수 | `Accel`·`Brake` 두 키를 **모두** 가진다. 키 이름과 대소문자는 고정 |
| `limit_point.Accel` | number \| null | 필수 | 단일 값(배열이 아니다) |
| `limit_point.Brake` | number \| null | 필수 | 단일 값(배열이 아니다) |
| `zero_brake_angle` | number \| null | 필수 | 단일 값 |
| 모든 수치 값 | number \| null | | 유한한 수 또는 `null`(§6). `NaN`·`Infinity`는 오지 않는다 |

- 값의 의미(어느 축인지, 어떤 보정에 쓰는지)와 **단위는 로봇 쪽 정의를 따른다.** 앱은
  사용자 입력을 **순서 그대로** 전달하며 단위 변환·반올림을 하지 않는다. 단위는
  미확정이다(§9).
- `calibration_data`는 앱이 값을 해석하지 않고 그대로 넘긴다. 소수점 자리수를 줄이지
  않으므로 위 예처럼 유효숫자가 긴 값이 온다.
- 배열 길이(`point_angle.*` = 2, `calibration_data` = 2)와 키 구성이 다르면
  `400 SETTINGS_INVALID`로 거절한다.
- 허용 범위는 로봇이 정한다. 범위를 벗어나면 `422 SETTINGS_OUT_OF_RANGE`로 거절하고
  `error.message`에 허용 범위를, `error.details.field`에 **경로 표기**
  (`point_angle.Accel[1]`, `limit_point.Brake`, `calibration_data[0]`,
  `zero_brake_angle`)를 적는다.
- 위 네 키 외의 최상위 키는 보내지 않는다. 나중에 `ar_trapezoidal_step` /
  `pf_straight_line`이 확정되면 최상위 키로 추가되고 이 문서를 갱신한다(§9).
  **모르는 최상위 키가 오면 무시하지 말고 `400 SETTINGS_INVALID`로 거절한다** —
  설정이 조용히 빠진 채 시험이 돌아가는 것을 막기 위한 규칙이다.

**성공 응답 200** — 검증하고 **실제로 적용한 뒤** 보낸다.

```json
{ "ok": true, "session_id": "3f1c…", "sequence": 2, "state": "ready", "message": "설정 적용 완료" }
```

**오류값**

| HTTP | code | 상황 |
| --- | --- | --- |
| 400 | `BAD_REQUEST` | JSON 파싱 실패, 공통 헤더 누락 |
| 400 | `SETTINGS_INVALID` | 최상위 키 누락·추가, 하위 키 부족, 배열 길이가 2가 아님, 숫자가 아님 |
| 409 | `SESSION_CONFLICT` | 다른 세션이 실행 중 |
| 409 | `SEQUENCE_REGRESSED` | 명령 번호 역행 |
| 409 | `ALREADY_RUNNING` | 실행 중에는 설정을 바꾸지 않는다 |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | `Content-Type`이 JSON이 아님 |
| 422 | `SETTINGS_OUT_OF_RANGE` | 값이 장비 허용 범위를 벗어남 |
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

### 4.3 `POST /api/v1/test/target` — 시험시나리오 CSV

`Content-Type: text/csv; charset=utf-8`

**요청 본문** — `target.csv` 내용을 **그대로** 담는다. multipart 아님, Base64 아님.
앱에 저장된 파일과 바이트 단위로 같다.

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
| 줄 끝 | CRLF(`\r\n`) — 수신 시 LF도 받아들이기를 권한다 |
| 인코딩 | UTF-8, BOM 없음 |
| `time` | 초 단위. 0 이상, 행마다 **증가**해야 한다 |
| `target_v` | 그 시각의 목표값. 유한한 수. 지수 표기(`4.82E-06`) 허용 |
| 행 수 | 데이터 1행 이상. 상한은 미확정(§9) |

**추가 헤더**

| 헤더 | 필수 | 설명 |
| --- | --- | --- |
| `X-Filename` | 선택 | 항상 `target.csv` |
| `X-Row-Count` | 선택 | 머리글을 제외한 데이터 행 수. 수신 누락 검출에 쓴다 |
| `Content-Length` | 필수 | 본문 바이트 수 |

- **시나리오가 없는 시험은 이 요청을 보내지 않는다.** 빈 CSV나 머리글만 있는 CSV는
  오지 않는다. "목표값 없음"과 "점이 없는 곡선"을 구별하기 위한 규칙이다.
  목표값 없이 구동할 수 없는 장비는 `/start`에서 `409 NO_TARGET`으로 거절한다.
- 수용 한도(행 수·바이트)를 정하고 넘으면 `413 TARGET_TOO_LARGE`. 본문을 메모리에
  무한정 받지 않는다.

**성공 응답 200**

```json
{ "ok": true, "session_id": "3f1c…", "sequence": 2, "state": "ready",
  "message": "target.csv 1200행 적재" }
```

**오류값**

| HTTP | code | 상황 |
| --- | --- | --- |
| 400 | `BAD_REQUEST` | 공통 헤더 누락, 본문 없음 |
| 409 | `SETTINGS_REQUIRED` | `/settings`보다 먼저 왔다 |
| 409 | `SESSION_CONFLICT` / `SEQUENCE_REGRESSED` / `ALREADY_RUNNING` | §6 |
| 413 | `TARGET_TOO_LARGE` | 수용 한도 초과 |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | `Content-Type`이 `text/csv`가 아님 |
| 422 | `TARGET_INVALID` | 머리글 불일치, 숫자 아님, 시각이 증가하지 않음, 데이터 행 없음 |
| 422 | `TARGET_ROW_COUNT_MISMATCH` | `X-Row-Count`와 실제 행 수가 다름 |

`TARGET_INVALID`는 `error.details`에 **문제가 된 행 번호**를 담는다
(`{"row": 417, "reason": "time_not_increasing"}`).

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

### 4.4 `POST /api/v1/test/start` — 실제 구동 시작

`Content-Type: application/json; charset=utf-8`

**요청 본문** — 직전에 세팅한 것이 맞는지 확인하는 값만 담는다.

```json
{ "test_id": "demo-0", "test_revision": 2 }
```

| 필드 | 형 | 필수 | 설명 |
| --- | --- | --- | --- |
| `test_id` | string | 필수 | `X-Test-Id`와 같은 값 |
| `test_revision` | int | 필수 | `X-Test-Revision`과 같은 값 |

이 값이 `/settings`에서 받은 값과 다르면 `409 TEST_MISMATCH`로 거절한다. 다른 시험의
설정으로 시작하는 것을 막는 확인이다.

**성공 응답 200** — 구동을 **실제로 시작한 뒤** 답한다. 시험 완료를 기다리지 않는다.
이 응답이 앱이 받는 마지막 응답이며, 앱은 받고 나서 연결을 닫는다.

```json
{ "ok": true, "session_id": "3f1c…", "sequence": 3, "state": "running", "message": "시험 시작" }
```

**오류값**

| HTTP | code | 상황 |
| --- | --- | --- |
| 400 | `BAD_REQUEST` | 본문 파싱 실패, 필드 누락, 공통 헤더 누락 |
| 409 | `SETTINGS_REQUIRED` | `/settings`를 받지 못했다 |
| 409 | `NO_TARGET` | 목표값이 필요한데 `/target`을 받지 못했다 |
| 409 | `TEST_MISMATCH` | 본문의 시험/버전이 세팅과 다름 |
| 409 | `ALREADY_RUNNING` | 이미 실행 중 |
| 409 | `SESSION_CONFLICT` / `SEQUENCE_REGRESSED` | §6 |
| 500 | `DEVICE_ERROR` | 시작 중 내부 오류, 시작 확인 실패 |
| 503 | `DEVICE_NOT_READY` | 인터록·비상정지 등으로 준비되지 않음 |

**curl 예**

```bash
curl -i -X POST http://192.0.2.10:8080/api/v1/test/start \
  -H 'Content-Type: application/json; charset=utf-8' \
  -H 'X-Session-Id: 3f1c0e8a9b6d4f2a8c7e5b1d0a9f8e7c' \
  -H 'X-Sequence: 2' -H 'X-Test-Id: demo-0' -H 'X-Test-Revision: 2' \
  -d '{"test_id":"demo-0","test_revision":2}'
```

---

## 5. 상태값(`state`)

응답마다 실리는 현재 상태다. 조회용 엔드포인트는 없다(§8).

| 값 | 뜻 | 전이 |
| --- | --- | --- |
| `idle` | 세팅 없음, 대기 | `/settings` 성공 → `ready` |
| `ready` | 세팅 완료, 시작 대기 | `/start` 성공 → `running` |
| `running` | 시험 구동 중 | 구동 종료 → `idle`(정상) / `error`(오류) |
| `error` | 로봇 오류 | 새 세션의 `/settings` 성공 → `ready` |

표에 없는 문자열을 보내면 앱은 그 문자열을 그대로 상태 표시에 쓴다. 가능하면 네 값만 쓴다.

**구동 종료 후 세션 해제 (반드시 구현)**
규약에 중지가 없으므로 앱은 `running`을 되돌릴 수 없다. **구동이 끝나면 서버가 스스로
세션을 해제하고 `idle`로 돌아가야 한다.** 그러지 않으면 두 번째 시험이 영구히
`SESSION_CONFLICT`로 거절된다. 정상 종료·오류 종료·비상정지 모두 같은 해제 경로를
거친다. 운용자가 수동으로 상태를 되돌릴 수단(조작반, CLI, 서버 재시작)을 하나 남긴다.

---

## 6. 세션·명령 번호·`null` 규칙

**세션**

- `X-Session-Id`는 한 번의 시험 실행을 가리킨다. 앱이 시작을 누를 때 새로 만든다.
- 서버는 **현재 세션 하나만** 유지한다. `/settings`를 받으면 그 세션이 현재 세션이 된다.
- 현재 세션이 `running`인데 다른 `X-Session-Id`로 요청이 오면 `409 SESSION_CONFLICT`.
  두 대의 태블릿이 같은 로봇을 동시에 잡는 것을 막는 규칙이다.
- 세션이 바뀌면 이전 세션의 설정·시나리오·상태를 버린다.

**명령 번호**

- `X-Sequence`는 같은 세션 안에서 **감소하지 않는다.** 더 작은 번호가 오면
  `409 SEQUENCE_REGRESSED`. 지연 도착한 옛 명령을 적용하지 않기 위한 규칙이다.
- `/settings`와 `/target`은 한 `configure` 명령의 두 요청이므로 같은 번호가 온다.
  같은 번호의 재전송은 허용한다.
- 같은 `(session_id, sequence)` 요청을 다시 받으면 **멱등**하게 처리한다. 특히
  `/start`를 두 번 구동하지 않는다. 앱은 자동 재시도하지 않지만(§8) 네트워크 재전송은
  있을 수 있다.

**`null`의 취급**

- 앱은 사용자가 입력하지 않은 값을 `null`로 보낸다. **0으로 바꿔 보내지 않는다.**
  `0`은 "설정된 0"이고 `null`은 "아직 없음"이다.
- 서버는 `null`을 기본값으로 대체해 조용히 진행하지 않는다. 구동에 필요한 값이
  `null`이면 `422 SETTINGS_INCOMPLETE`로 거절하고 어떤 필드인지 `error.details`에 담는다.
- 어떤 값이 구동 필수인지는 로봇 쪽 요구사항이다(미확정 → §9).

---

## 7. 오류값 전체 표

서버가 `error.code`로 보낼 수 있는 값은 아래가 전부다. 표에 없는 코드를 보내면 앱은
일괄로 "로봇이 거절함"으로 처리하므로, 사용자는 `error.message`만 보게 된다.

| `error.code` | HTTP | 뜻 | 발생 엔드포인트 |
| --- | --- | --- | --- |
| `BAD_REQUEST` | 400 | 요청 형식·헤더 오류, 파싱 실패 | 전부 |
| `UNSUPPORTED_MEDIA_TYPE` | 415 | `Content-Type` 불일치 | settings, target |
| `SETTINGS_INVALID` | 400 | `/settings` 본문의 구조/형 오류 | settings |
| `SETTINGS_OUT_OF_RANGE` | 422 | 값이 장비 허용 범위 밖 | settings |
| `SETTINGS_INCOMPLETE` | 422 | 구동에 필요한 값이 `null` | settings |
| `SETTINGS_REQUIRED` | 409 | 세팅 전에 다음 단계가 왔다 | target, start |
| `TARGET_INVALID` | 422 | CSV 내용 오류 | target |
| `TARGET_ROW_COUNT_MISMATCH` | 422 | `X-Row-Count`와 실제 행 수 불일치 | target |
| `TARGET_TOO_LARGE` | 413 | CSV가 수용 한도 초과 | target |
| `NO_TARGET` | 409 | 목표값 없이 시작 요청 | start |
| `TEST_MISMATCH` | 409 | 시작 요청의 시험/버전 불일치 | start |
| `SESSION_CONFLICT` | 409 | 다른 세션이 점유 중 | settings, target, start |
| `SEQUENCE_REGRESSED` | 409 | 명령 번호 역행 | settings, target, start |
| `ALREADY_RUNNING` | 409 | 이미 실행 중 | settings, target, start |
| `DEVICE_NOT_READY` | 503 | 장치 준비 안 됨(인터록, 비상정지) | health, start |
| `DEVICE_ERROR` | 500 | 로봇 내부 오류 | 전부 |

**앱이 스스로 만드는 오류** — 서버가 보내는 값이 아니다. 참고용이며, 아래가 뜨면
서버가 응답을 못 했거나 규약을 어긴 것이다.

| 앱 내부 코드 | 뜻 | 서버 쪽 원인 |
| --- | --- | --- |
| `ROBOT_UNREACHABLE` | 연결 거부·주소 오류 | 서버가 떠 있지 않다 / 다른 포트 / 방화벽 |
| `ROBOT_TIMEOUT` | 3초 내 응답 없음 | 응답 전에 구동 완료를 기다렸다 / 블로킹 |
| `ROBOT_REPLY_INVALID` | JSON이 아니거나 `ok` 누락 | 빈 본문, HTML 오류 페이지, 잘못된 `Content-Type` |
| `ROBOT_REPLY_MISMATCH` | `session_id`/`sequence` echo 불일치 | echo 규칙 위반(§3.1) |

---

## 8. 규약에 없는 것 — 상태 조회·중지·재시도

**`GET /status`와 `POST /stop`은 규약에 없다.** 앱이 보내지 않기로 결정한 것이며
서버가 구현할 필요도 없다. 그래서 다음이 따라온다. 양쪽이 같이 알고 있어야 한다.

| 결과 | 내용 |
| --- | --- |
| 시작 이후 로봇 상태를 모른다 | 앱 화면의 로봇 상태는 `/start` 응답의 `state`에서 멈춘다 |
| 앱이 로봇을 멈출 수 없다 | 앱에서 실험을 중지하거나 창을 닫아도 **로봇은 계속 구동한다** |
| 수신 지연 감지가 없다 | 랜선이 빠져도 앱은 시작이 성공한 것으로 남는다 |
| 앱 기록에 로봇 진행이 남지 않는다 | 보낸 설정과 시작 성공 여부만 남는다 |

**안전** — 앱은 중지 신호를 보내지 않는다. **시작한 시험을 멈추는 수단은 로봇 쪽에만
있어야 한다**(조작반·비상정지). 앱을 닫으면 로봇이 멈춘다고 가정한 설계를 하지 않는다.
서버 프로세스가 죽어도 구동이 안전 상태로 가야 한다. HTTP 서버가 구동의 생명줄이 되면 안 된다.

**재시도** — 앱은 실패한 POST를 자동으로 다시 보내지 않는다(`/start` 중복 구동 위험).
실패는 사용자에게 알리고 사용자가 다시 시작한다. 그래서 서버는 거절 뒤에도 깨끗한
상태로 남아 다음 시도를 받을 수 있어야 한다.

| 요청 | 앱 타임아웃 | 재시도 |
| --- | --- | --- |
| `GET /health` | 3초 | 없음 |
| `POST /settings`, `/target`, `/start` | 각 3초 | 없음 |

---

## 9. 확정이 필요한 항목

추측해서 구현하지 말고 값을 정해 앱 담당자에게 알린다. 확정되면 이 문서를 같은
작업에서 갱신한다.

| 항목 | 상태 |
| --- | --- |
| 로봇 IP와 포트 (제안 8080) | 미확정 |
| `point_angle` 배열 두 원소의 의미·단위·허용 범위 | 미확정 |
| `calibration_data` 두 원소의 의미·단위·허용 범위 | 미확정 |
| `limit_point.Accel` / `limit_point.Brake`의 의미·단위·허용 범위 | 미확정 |
| `zero_brake_angle`의 단위·허용 범위 | 미확정 |
| 구동 필수 값 (= `SETTINGS_INCOMPLETE` 판단 기준) | 미확정 |
| `target.csv` 최대 행 수 / 최대 바이트 | 미확정 |
| 목표 곡선 없이 구동 가능한가 (= `NO_TARGET` 적용 여부) | 미확정 |
| 구동 종료 후 세션 해제 시점(완료 신호의 출처) | 미확정 |
| 시작한 시험을 멈추는 로봇 쪽 절차 | 미확정 |
| `ar_trapezoidal_step` / `pf_straight_line` 전송 규격 | 미확정 — 확정 시 `/settings` 본문의 최상위 키로 추가 |

**이 규약에 포함되지 않은 것**

- 상태 조회·중지: §8.
- 인증·암호화: 랜선 직결 구간 전용이므로 없다. 외부망에 노출하면 안 된다.
- 로봇 → 앱 측정 데이터 스트림: 이 규약은 제어 경로만 다룬다. 측정값은 별도 경로
  (HI-EDGE WebSocket)로 앱에 들어온다. 로봇 서버가 관여하지 않는다.

---

## 10. 구현 체크리스트

하드웨어 없이 전부 확인할 수 있는 항목이다. 그대로 검증 목록으로 쓴다.

**응답 양식**
- [ ] 성공·오류 모두 JSON 본문이 있고 `Content-Type`이 `application/json; charset=utf-8`
- [ ] 모든 응답에서 `session_id`·`sequence`가 요청 헤더와 글자 단위로 같다
- [ ] 모든 응답에 `ok`와 `state`가 있다
- [ ] 모든 오류에 `error.code`와 **한국어** `error.message`가 있다
- [ ] 알 수 없는 경로·메서드도 JSON으로 답한다(404/405)

**요청 검증**
- [ ] 공통 헤더 누락 → `400 BAD_REQUEST`
- [ ] `Content-Type` 불일치 → `415 UNSUPPORTED_MEDIA_TYPE`
- [ ] 최상위 네 키 중 하나라도 없거나, 규약에 없는 키가 섞여 있으면 → `SETTINGS_INVALID`
- [ ] `point_angle` 하위 키 누락 / 길이 2 아님 / 숫자 아님 → `SETTINGS_INVALID`
- [ ] `calibration_data` 길이 2 아님, `limit_point` 키 부족 → `SETTINGS_INVALID`
- [ ] `limit_point.*`·`zero_brake_angle`에 배열이 오면 → `SETTINGS_INVALID` (단일 값이다)
- [ ] 필수 값이 `null` → `SETTINGS_INCOMPLETE`, 값이 `0`이면 정상 통과
- [ ] CSV 머리글 불일치 / 시각 비증가 / 데이터 행 0 → `TARGET_INVALID` (행 번호 포함)
- [ ] `X-Row-Count` 불일치 → `TARGET_ROW_COUNT_MISMATCH`

**순서·상태**
- [ ] `/settings` 없이 `/target` → `SETTINGS_REQUIRED`
- [ ] `/settings` 없이 `/start` → `SETTINGS_REQUIRED`
- [ ] `/start` 본문의 시험/버전 불일치 → `TEST_MISMATCH`
- [ ] 번호 역행 → `SEQUENCE_REGRESSED`, 실행 중 다른 세션 → `SESSION_CONFLICT`
- [ ] 같은 `(session_id, sequence)` 재전송 → 멱등(구동 1회)
- [ ] `/settings`·`/target`만 받은 상태에서 장비가 **움직이지 않는다**
- [ ] `/start` 응답이 3초 안에 오고, 시험 완료를 기다리지 않는다
- [ ] 구동 종료 후 `idle`로 돌아가 **두 번째 시험을 받는다**

**견고성**
- [ ] 요청 하나의 예외로 서버가 죽지 않는다(최상단에서 잡아 `500 DEVICE_ERROR`)
- [ ] 상태를 바꾸는 요청을 직렬로 처리한다(락). 동시 요청이 세션 상태를 교차 수정하지 않는다
- [ ] 받은 요청마다 로그 한 줄(메서드·경로·HTTP·`error.code`) + **받은 값 요약**
      (`point_angle` 세 쌍, `calibration_data` 두 값, `limit_point` 두 값,
      `zero_brake_angle`, CSV 행 수와 첫/끝 점, 시험 ID/리비전). 200이 떴는데 값이
      제대로 왔는지는 따로 확인해야 하기 때문이다

**연결 확인**

```bash
curl http://<로봇 IP>:8080/api/v1/health
```

그 다음 §4의 curl 예를 1→2→3→4 순서로 보내 전 구간을 확인하고, 마지막으로 앱에서
**시작**을 눌러 확인한다.
