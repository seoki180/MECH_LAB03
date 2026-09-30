"""기록 파일(.jsonl)에서 결과 그래프용 값을 읽어 온다.

실험이 끝난 뒤 쓰인다. 화면에 그릴 값은 **메모리의 최신 스냅샷이 아니라 기록 파일**에서
읽는다. 스냅샷은 채널마다 마지막 값 하나만 들고 있어 곡선을 그릴 수 없고, 기록이야말로
그 실행에서 실제로 남은 자료다.

파일 첫 줄의 snapshot에는 실행 시점에 고정된 시나리오가 들어 있다. 그래서 실행 뒤
target.csv를 바꾸거나 지워도 이 화면은 그때의 목표값을 그린다.
"""
import json
from pathlib import Path

from experiment_app.domain.analysis import analyse
from experiment_app.domain.scenario import ScenarioPoint
from experiment_app.domain.test_definition import AppError


class AnalysisService:
    def __init__(self, results, sessions):
        self.results = results
        # 채널 목록은 sessions에서 그때그때 읽는다. 장치 탭에서 NMEA 파일을 바꾸면
        # set_sources로 채널이 교체되므로, 부팅 시점 목록을 들고 있으면 라벨·단위가
        # 화면과 어긋난다.
        self.sessions = sessions

    @property
    def channels(self):
        return self.sessions.channels

    def channel_for(self, part):
        """part의 첫 채널 (part, id, label, unit). 없으면 None."""
        return next((entry for entry in self.channels if entry[0] == part), None)

    def analyse_session(self, session_id, part="C"):
        """세션의 기록을 읽어 ResultAnalysis를 만든다.

        기본 채널은 C 영역 첫 채널(현재 속도)이다. 시나리오의 target_v가 속도
        목표이므로 속도 채널과 견주는 것이 자료의 뜻에 맞는다.
        """
        summary = self.results.get(session_id)
        path = summary.get("recording_path") or ""
        if not path or not Path(path).is_file():
            raise AppError("RECORDING_MISSING",
                           "이 실행의 기록 파일을 찾을 수 없어 그래프를 그릴 수 없습니다.")
        entry = self.channel_for(part)
        if entry is None:
            raise AppError("VALIDATION_FAILED", f"{part} 영역 채널이 없습니다.")
        _, channel_id, label, unit = entry
        scenario, samples = self._read(path, channel_id)
        return analyse(samples, scenario, channel_id, label, unit)

    @staticmethod
    def _read(path, channel_id):
        """기록을 한 줄씩 읽어 시나리오와 해당 채널 표본만 모은다.

        한 줄이 깨져도 그 줄만 건너뛴다. 기록 중 강제 종료로 마지막 줄이 잘리는 일이
        있는데, 그 때문에 앞의 정상 자료를 모두 버리지 않는다.
        """
        scenario, samples = (), []
        with open(path, encoding="utf-8") as handle:
            for number, line in enumerate(handle):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if number == 0 and "snapshot" in record:
                    scenario = tuple(ScenarioPoint(float(point["time"]), float(point["target_v"]))
                                     for point in record["snapshot"].get("scenario", ()))
                    continue
                if record.get("channel_id") != channel_id:
                    continue
                samples.append((record.get("monotonic_received"), record.get("value"),
                                record.get("quality")))
        return scenario, samples
