from dataclasses import dataclass
from datetime import datetime, timezone
from experiment_app.domain.telemetry import effective_quality


@dataclass(frozen=True)
class MetricViewModel:
    label: str
    value_text: str
    unit: str
    quality_label: str
    last_updated: str
    age: float


def metric_model(label, unit, sample, now, live=True):
    if sample is None:
        return MetricViewModel(label, "—", unit, "수신 대기", "—", 0)
    age = max(0, now - sample.monotonic_received)
    quality = effective_quality(sample, now, live)
    if not live and quality == "정상":
        quality = "종료 시 값"
    value = "—" if sample.value is None else f"{sample.value:.2f}"
    return MetricViewModel(label, value, unit, quality, sample.received_time_utc[11:19] + " UTC", age)


def format_created(created_utc):
    """생성일자를 사람이 읽는 현지 시각으로. 값이 없으면 대시를 돌려준다.

    저장은 타임존이 있는 UTC이고 표시만 현지 시각으로 바꾼다. 비어 있는 것을
    현재 시각으로 꾸미지 않는다.
    """
    if not created_utc:
        return "—"
    try:
        moment = datetime.fromisoformat(created_utc)
    except ValueError:
        # 사람이 손으로 고친 값. 읽은 그대로 보여주고 해석하지 않는다.
        return created_utc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone().strftime("%Y-%m-%d %H:%M")


@dataclass(frozen=True)
class ScenarioViewModel:
    """시험시나리오 표시 상태. 없음/오류/정상 세 가지를 구분한다."""
    state: str  # "none" | "error" | "ready"
    summary: str
    detail: str = ""
    file_name: str = ""

    @property
    def runnable(self):
        """시나리오가 없어도 실행할 수 있다. 깨진 파일만 막는다.

        없음은 사용자가 목표값을 주지 않기로 한 것이므로 선택이다. 반면 오류는
        파일이 있는데 읽지 못한 것이라, 그대로 실행하면 사용자가 의도한 목표값과
        다른 시험이 된다. 둘을 같이 취급하지 않는다.
        """
        return self.state != "error"


def scenario_model(points, error=None, file_name=""):
    if error is not None:
        return ScenarioViewModel("error", "시나리오 오류", str(error), file_name)
    if not points:
        return ScenarioViewModel("none", "시나리오 없음",
                                 "목표값 없이 실행합니다. 필요하면 CSV(time,target_v)를 가져오세요.", file_name)
    duration = points[-1].time
    return ScenarioViewModel("ready", f"{len(points)}행 · {duration:g}초",
                             f"목표값 {points[0].target_v:g} → {points[-1].target_v:g}", file_name)
