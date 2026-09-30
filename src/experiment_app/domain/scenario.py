"""시험시나리오(target 속도 곡선) 표현과 CSV 해석.

로봇에 한 번 보내는 시험 설정(.json)과 달리, 시나리오는 시간에 따른 목표값의
나열이다. 자료 양식은 ``asset/UI작업 관련자료/데이터 흐름/10km_target.csv`` 를
따른다. 머리글은 ``time,target_v`` 이고 각 행은 초 단위 시각과 그때의 목표값이다.

파일 I/O와 wx는 여기 들어오지 않는다. 문자열 → 값, 값 → 문자열만 담당한다.
"""
from dataclasses import dataclass
import csv
import io
import math

from .test_definition import AppError

SCENARIO_HEADER = ("time", "target_v")
SCENARIO_SUFFIX = ".csv"


@dataclass(frozen=True)
class ScenarioPoint:
    time: float
    target_v: float


def parse_scenario_csv(body, source=""):
    """CSV 본문을 ScenarioPoint 열로 바꾼다. 형식이 어긋나면 AppError를 낸다.

    - BOM과 CRLF를 허용한다. 자료 파일이 Excel에서 저장돼 둘 다 들어 있다.
    - 지수 표기(``4.82E-06``)를 그대로 받는다.
    - 시각은 0 이상이고 증가해야 한다. 뒤섞인 행은 조용히 정렬하지 않고 거부한다.
    """
    where = f" ({source})" if source else ""
    rows = list(csv.reader(io.StringIO(body.lstrip("\ufeff"))))
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        raise AppError("SCENARIO_INVALID", f"시나리오 CSV가 비어 있습니다{where}.")
    header = tuple(cell.strip().lstrip("\ufeff").casefold() for cell in rows[0][:2])
    if header != SCENARIO_HEADER:
        raise AppError("SCENARIO_INVALID",
                       f"첫 줄은 '{','.join(SCENARIO_HEADER)}' 여야 합니다{where}. 읽은 값: {','.join(rows[0][:2])}")
    points = []
    previous = None
    for number, row in enumerate(rows[1:], start=2):
        if len(row) < 2:
            raise AppError("SCENARIO_INVALID", f"{number}번째 줄에 값이 부족합니다{where}.")
        try:
            time, target = float(row[0]), float(row[1])
        except ValueError:
            raise AppError("SCENARIO_INVALID", f"{number}번째 줄을 숫자로 읽을 수 없습니다{where}.") from None
        if not (math.isfinite(time) and math.isfinite(target)):
            raise AppError("SCENARIO_INVALID", f"{number}번째 줄에 유한하지 않은 값이 있습니다{where}.")
        if time < 0:
            raise AppError("SCENARIO_INVALID", f"{number}번째 줄의 시각이 음수입니다{where}.")
        if previous is not None and time <= previous:
            raise AppError("SCENARIO_INVALID", f"{number}번째 줄의 시각이 증가하지 않습니다{where}.")
        previous = time
        points.append(ScenarioPoint(time, target))
    if not points:
        raise AppError("SCENARIO_INVALID", f"시나리오에 데이터 행이 없습니다{where}.")
    return tuple(points)


def format_scenario_csv(points):
    """ScenarioPoint 열을 자료 양식과 같은 CSV 본문으로 되돌린다."""
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\r\n")
    writer.writerow(SCENARIO_HEADER)
    for point in points:
        writer.writerow((repr(point.time), repr(point.target_v)))
    return stream.getvalue()


def scenario_duration(points):
    """시나리오 전체 길이(초). 비어 있으면 0."""
    return points[-1].time if points else 0.0
