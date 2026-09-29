from dataclasses import dataclass
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
