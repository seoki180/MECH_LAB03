#!/usr/bin/env python3
"""HI-EDGE LAN 스트림 연결 점검.

GUI를 띄우지 않고 이더넷 연결·인증서·메시지 규격을 확인한다. 현장에서
"시작했는데 값이 안 온다"의 원인이 연결인지 장비 상태인지 가른다.

    python -m tools.lan_probe --host 169.254.32.88
    python -m tools.lan_probe --host 192.168.33.1 --skip-verify
    python -m tools.lan_probe --watch 10      # 10초 동안 받은 것을 요약

``--skip-verify`` 는 장비 공개 인증서(hi-edge-ui-cert.pem)가 아직 없을 때만 쓴다.
접속 대상이 장비인지 확인하지 않으므로 운용에서는 쓰지 않는다.
"""

import argparse
import json
import ssl
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from experiment_app.domain.test_definition import AppError  # noqa: E402
from experiment_app.infrastructure import hiedge  # noqa: E402


def watch(host, port, verify, seconds):
    """일정 시간 동안 받은 메시지를 요약한다. 값을 지어내지 않고 센 것만 적는다."""
    from websockets.sync.client import connect

    tls = hiedge.tls_context(None, verify)
    url = hiedge.stream_url(host, port)
    kinds = Counter()
    total = valid = gaps = 0
    coordinates = 0
    first_speed = last_speed = None
    with connect(url, ssl=tls, proxy=None, compression=None,
                 open_timeout=6, close_timeout=1, max_size=65536) as ws:
        hello = json.loads(ws.recv(timeout=6))
        print(f"hello: stream_id={hello.get('stream_id')} "
              f"{hello.get('publication_hz')} Hz replay={hello.get('replay')}")
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            message = json.loads(ws.recv(timeout=3))
            if message.get("type") != "speed":
                continue
            total += 1
            if message.get("source_gap"):
                gaps += 1
            speed = hiedge._speed_kmh(message)
            if speed is not None:
                valid += 1
                first_speed = speed if first_speed is None else first_speed
                last_speed = speed
                estimate = message.get("estimate") or {}
                kinds[estimate.get("measurement_kind")] += 1
            latitude, _ = hiedge._coordinates(message)
            if latitude is not None:
                coordinates += 1
    print(f"\n{seconds}초 동안 {total}개 수신 · 유효 {valid} · 단절표시 {gaps}")
    print(f"좌표 포함 메시지: {coordinates}" + ("" if coordinates else "  (아직 좌표가 오지 않습니다)"))
    if kinds:
        print("measurement_kind: " + ", ".join(f"{k}={v}" for k, v in kinds.items()))
    if valid:
        print(f"속도 처음 {first_speed:.3f} / 마지막 {last_speed:.3f} km/h")
    else:
        print("유효한 속도가 한 번도 오지 않았습니다. 장비의 GNSS 상태를 확인하세요.")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=hiedge.DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=hiedge.DEFAULT_PORT)
    parser.add_argument("--skip-verify", action="store_true",
                        help="인증서 검증을 생략한다. 공개 인증서가 아직 없을 때만 쓴다.")
    parser.add_argument("--watch", type=float, default=0,
                        help="연결만 확인하지 않고 이 초 동안 받은 것을 요약한다.")
    args = parser.parse_args()
    verify = not args.skip_verify
    if args.skip_verify:
        print("주의: 인증서 검증을 생략합니다. 접속 대상이 장비인지 확인하지 않습니다.\n")
    try:
        if args.watch:
            watch(args.host, args.port, verify, args.watch)
        else:
            print(hiedge.probe(args.host, args.port, verify=verify))
    except AppError as error:
        print(f"{error.code}: {error}", file=sys.stderr)
        return 2
    except ssl.SSLError as error:
        print(f"TLS 오류: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n중단했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
