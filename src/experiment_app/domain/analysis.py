"""측정값과 시험시나리오(target)를 견주는 계산.

결과 그래프가 쓰는 값을 여기서 만든다. wx와 파일 I/O는 들어오지 않는다.

시각 기준: 측정 표본에는 세션 시작부터의 경과 시간이 따로 기록되지 않고 단조 시계
수신 시각(``monotonic_received``)만 있다. 그래서 **첫 표본을 0초로 두고** 상대 시각을
만든다. 시나리오의 시각도 0부터 시작하므로 두 축이 같은 기준이 된다. 재생 소스는
실시간으로 재생되므로 이 상대 시각이 파일 안의 시각과 거의 같다.

편차는 ``측정값 − 목표값``이다. 목표값이 없는 구간은 편차를 만들지 않는다 — 곡선
밖을 0으로 채우면 '목표를 정확히 맞춘 구간'과 구별되지 않는다.
"""
from bisect import bisect_left
from dataclasses import dataclass, field


def usable_quality(quality):
    """정상 데모 표본과 유효한 NMEA INS fix만 그래프에 사용한다.

    NMEA 소스는 유효한 fix의 원본 품질을 'NMEA · INS_…'로 기록한다.
    'fix 없음', 지연, 연결 끊김 같은 오류 표시는 허용하지 않는다.
    """
    return quality in (None, "정상") or (
        isinstance(quality, str) and quality.startswith("NMEA · INS_"))


@dataclass(frozen=True)
class Series:
    """그래프 한 줄. points는 (시각 초, 값) 오름차순."""
    label: str
    unit: str
    points: tuple[tuple[float, float], ...] = ()

    def __bool__(self):
        return bool(self.points)

    @property
    def time_range(self):
        return (self.points[0][0], self.points[-1][0]) if self.points else (0.0, 0.0)

    @property
    def value_range(self):
        if not self.points:
            return (0.0, 0.0)
        values = [value for _, value in self.points]
        return (min(values), max(values))


@dataclass(frozen=True)
class ResultAnalysis:
    """결과 그래프 두 개가 필요한 값 전체.

    measured/target이 비어 있을 수 있다. 화면은 '값 없음'을 표시하고 축을 지어내지
    않는다. skipped는 값이 없거나(None) 품질이 정상이 아니라 제외한 표본 수다.
    """
    measured: Series
    target: Series
    deviation: Series
    skipped: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def comparable(self):
        """편차를 낼 수 있는 구간이 있는지."""
        return bool(self.deviation)


def target_at(points, when):
    """시나리오의 ``when`` 시점 목표값. 곡선 밖이면 None.

    두 점 사이는 직선으로 잇는다. 곡선 밖을 바깥으로 늘려 짐작하지 않는다 —
    시나리오가 끝난 뒤의 목표값은 자료에 없는 값이다.
    """
    if not points:
        return None
    times = [point.time for point in points]
    if when < times[0] or when > times[-1]:
        return None
    index = bisect_left(times, when)
    if times[index] == when:
        return points[index].target_v
    before, after = points[index - 1], points[index]
    span = after.time - before.time
    if span <= 0:
        return after.target_v
    ratio = (when - before.time) / span
    return before.target_v + (after.target_v - before.target_v) * ratio


def deviation_series(measured, target_points, label="편차", unit=""):
    """측정 시각마다 ``측정값 − 목표값``. 목표값이 없는 시각은 건너뛴다."""
    points = []
    for when, value in measured.points:
        target = target_at(target_points, when)
        if target is not None:
            points.append((when, value - target))
    return Series(label, unit, tuple(points))


def analyse(samples, scenario, channel_id, label, unit):
    """기록에서 읽은 표본과 실행 시점 시나리오로 결과 그래프 값을 만든다.

    samples는 (monotonic_received, value, quality) 열이며 시각 오름차순이라고 가정하지
    않는다 — 기록 순서가 어긋나도 시각으로 정렬해 그린다.
    """
    usable, skipped = [], 0
    for monotonic, value, quality in samples:
        # 값이 없거나 품질이 정상이 아닌 표본은 그래프에서 뺀다. 0으로 바꾸지 않는다.
        if value is None or monotonic is None or not usable_quality(quality):
            skipped += 1
            continue
        usable.append((monotonic, value))
    usable.sort(key=lambda item: item[0])
    origin = usable[0][0] if usable else 0.0
    measured = Series(label, unit, tuple((monotonic - origin, value) for monotonic, value in usable))

    target_unit = unit if unit else ""
    target = Series("목표값", target_unit,
                    tuple((point.time, point.target_v) for point in scenario))
    deviation = deviation_series(measured, scenario, "편차 (측정−목표)", unit)

    notes = []
    if not measured:
        notes.append(f"{channel_id} 채널에서 그릴 수 있는 측정값이 없습니다.")
    if not target:
        notes.append("이 실행에는 시험시나리오가 없어 목표값과 편차를 그릴 수 없습니다.")
    elif measured and not deviation:
        notes.append("측정 시각이 시나리오 구간과 겹치지 않아 편차를 낼 수 없습니다.")
    if skipped:
        notes.append(f"값이 없거나 품질이 정상이 아닌 표본 {skipped}개는 제외했습니다.")
    return ResultAnalysis(measured, target, deviation, skipped, tuple(notes))
