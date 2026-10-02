"""사용자가 고른 설정을 다음 실행까지 남긴다.

지금 담는 것은 LAN 수신 주소뿐이다. 현장마다 PC와 장비의 IP 구성이 달라
(169.254 자동 구성, 192.168.33 직결 등) 실행할 때마다 다시 입력하게 두면
안 된다.

저장 위치는 ``data_dir``(실행 파일 옆 .mechlab) 아래 settings.json이다. 사람이
열어 고칠 수 있는 형식으로 두되, 앱이 읽을 때는 값을 믿지 않고 검사한다.
손으로 고쳐 깨진 파일 때문에 앱이 시작하지 못하면 안 되므로, 읽기 실패는
기본값으로 넘어가고 저장 실패는 호출부에 알린다.
"""

import json
from pathlib import Path


class SettingsStore:
    """settings.json 읽기/쓰기. 실패해도 앱 시작을 막지 않는다."""

    def __init__(self, path):
        self.path = Path(path)
        # 읽다가 생긴 문제를 시작 로그에 남기기 위해 보관한다.
        self.load_error = None

    def load(self):
        """저장된 설정. 파일이 없거나 깨졌으면 빈 dict."""
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            # 깨진 파일을 지우지 않는다. 사용자가 손으로 고친 내용일 수 있다.
            self.load_error = f"{self.path} 를 읽지 못해 기본값으로 시작합니다 ({error})"
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, data):
        """원자적으로 쓴다. 쓰다 만 파일이 남으면 다음 실행에서 읽지 못한다."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    # --- LAN 설정 ---

    def lan(self, defaults):
        """저장된 LAN 설정. 값이 이상하면 그 항목만 기본값으로 돌린다."""
        saved = self.load().get("lan")
        if not isinstance(saved, dict):
            return dict(defaults)
        settings = dict(defaults)
        host = saved.get("host")
        if isinstance(host, str) and host.strip():
            settings["host"] = host.strip()
        port = saved.get("port")
        # bool은 int의 하위형이라 따로 막는다. True가 포트 1이 되면 안 된다.
        if isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535:
            settings["port"] = port
        if isinstance(saved.get("verify"), bool):
            settings["verify"] = saved["verify"]
        return settings

    def save_lan(self, settings):
        """LAN 설정만 갱신한다. 다른 항목은 건드리지 않는다."""
        data = self.load()
        data["lan"] = {"host": settings.get("host"), "port": settings.get("port"),
                       "verify": settings.get("verify", True)}
        self.save(data)
