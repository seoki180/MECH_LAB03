import wx
from pathlib import Path
from experiment_app.infrastructure import hiedge
from experiment_app.ui.theme import (text, button, add, surface, section_bar, input_control,
                                     MUTED, DANGER)


class DevicesPage(wx.Panel):
    """센서·GPS 입력원을 고르는 설정 화면.

    두 모드가 있다. NMEA 파일 재생(데모)과 HI-EDGE LAN 실시간 수신이다. 어느 쪽이
    선택되어 있는지와 연결 상태를 항상 사실대로 보여준다. 연결되지 않은 것을
    연결된 것처럼 표시하지 않는다.
    """

    def __init__(self, parent, scenarios, on_scenario, on_nmea, nmea_path,
                 on_mode=None, on_probe=None, mode="nmea", lan_settings=None):
        super().__init__(parent)
        surface(self)
        self.on_mode, self.on_probe = on_mode, on_probe
        settings = lan_settings or {}
        root = wx.BoxSizer(wx.VERTICAL)
        add(root, text(self, "장치 연결", 22, weight="semibold"), border=24)

        # --- 입력원 선택 ---
        # 터치 조작 높이를 지키려고 RadioBox 대신 Choice를 쓴다. RadioBox의 각 행은
        # 약 26 DIP라 48 DIP 기준에 못 미친다.
        self.MODES = ("nmea", "lan")
        self.mode = wx.Choice(self, choices=["NMEA 파일 재생 (데모)", "LAN 실시간 수신 (HI-EDGE)"])
        self.mode.SetSelection(1 if mode == "lan" else 0)
        input_control(self.mode)
        self.mode.Bind(wx.EVT_CHOICE, self.change_mode)
        root.Add(text(self, "센서·GPS 입력원", 13, colour=MUTED), 0,
                 wx.LEFT | wx.RIGHT | wx.TOP, self.FromDIP(24))
        add(root, self.mode, border=24)

        facts = wx.FlexGridSizer(2, self.FromDIP((20, 10)))
        facts.AddGrowableCol(1)
        self.file_label = text(self, Path(nmea_path).name if nmea_path else "선택되지 않음", 14, weight="semibold")
        if nmea_path:
            self.file_label.SetToolTip(str(nmea_path))
        self.source_label = text(self, "", 14, weight="semibold")
        for label, value in (("센서와 GPS", self.source_label),
                             ("로봇", text(self, "데모 연결", 14)),
                             ("NMEA 파일", self.file_label),
                             ("A 영역", text(self, "속도, 시작점 거리", 14)),
                             ("B 영역", text(self, "횡방향, 종방향", 14)),
                             ("C 영역", text(self, "현재 속도, 누적 이동거리", 14)),
                             ("기록", text(self, "화면 10 Hz, 중지할 때까지 수집, 원본 JSONL 저장", 14))):
            facts.Add(text(self, label, 13, colour=MUTED), 0, wx.ALIGN_CENTER_VERTICAL)
            facts.Add(value, 1, wx.EXPAND)
        root.Add(facts, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, self.FromDIP(24))
        self.nmea_button = button(self, "NMEA 파일 선택", lambda: self.select_nmea(on_nmea))
        add(root, self.nmea_button, border=24, flags=wx.ALL | wx.ALIGN_LEFT)

        # --- LAN 설정 ---
        root.Add(section_bar(self, "LAN 실시간 수신"), 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, self.FromDIP(16))
        address = wx.FlexGridSizer(2, self.FromDIP((12, 10)))
        address.AddGrowableCol(1)
        self.host = wx.TextCtrl(self, value=str(settings.get("host") or hiedge.DEFAULT_HOST))
        self.port = wx.TextCtrl(self, value=str(settings.get("port") or hiedge.DEFAULT_PORT))
        for control in (self.host, self.port):
            input_control(control)
        for label, control in (("장비 주소", self.host), ("포트", self.port)):
            address.Add(text(self, label, 13, colour=MUTED), 0, wx.ALIGN_CENTER_VERTICAL)
            address.Add(control, 1, wx.EXPAND)
        root.Add(address, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, self.FromDIP(16))
        self.skip_verify = wx.CheckBox(self, label="인증서 검증 생략 (시험용)")
        self.skip_verify.SetValue(settings.get("verify") is False)
        add(root, self.skip_verify, border=16)
        add(root, text(self, f"공개 인증서 위치: {hiedge.certificate_path()}", 12, colour=MUTED), border=16)
        self.probe_button = button(self, "연결 시험", self.run_probe)
        # 주소를 고쳐도 적용 경로가 없으면 화면 값과 실제 접속 주소가 어긋난다.
        # LAN 모드에서 주소를 바꾼 뒤 누르면 즉시 반영하고 다음 실행까지 남긴다.
        self.apply_button = button(self, "주소 적용", self.apply_address)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.Add(self.apply_button, 0, wx.RIGHT, self.FromDIP(8))
        buttons.Add(self.probe_button, 0)
        root.Add(buttons, 0, wx.ALL | wx.ALIGN_LEFT, self.FromDIP(16))
        self.probe_result = text(self, "시험하지 않았습니다.", 13, colour=MUTED)
        add(root, self.probe_result, border=16)

        # --- 고장 주입 시나리오 ---
        root.Add(section_bar(self, "실패 상태 시나리오"), 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, self.FromDIP(16))
        self.scenario = wx.Choice(self, choices=list(scenarios))
        self.scenario.SetSelection(0)
        input_control(self.scenario)
        self.scenario.Bind(wx.EVT_CHOICE, lambda e: on_scenario(self.scenario.GetStringSelection()))
        add(root, self.scenario, border=16)
        add(root, text(self, "수집 시나리오는 다음 시작부터, 저장 실패는 바로 적용됩니다.", 12, colour=MUTED), border=16)
        self.SetSizer(root)
        self.apply_mode(mode)

    # --- 모드 ---

    def apply_mode(self, mode):
        """선택된 모드에 맞게 표시와 활성 상태를 맞춘다."""
        lan = mode == "lan"
        self.source_label.SetLabel("LAN 실시간 수신 (HI-EDGE)" if lan else "NMEA 파일 재생")
        self.nmea_button.Enable(not lan)
        self.file_label.Enable(not lan)
        for control in (self.host, self.port, self.skip_verify, self.probe_button, self.apply_button):
            control.Enable(lan)
        self.Layout()

    def change_mode(self, event):
        wanted = self.MODES[self.mode.GetSelection()]
        if self.on_mode is None:
            return
        # 적용이 거부되면 선택을 원래대로 돌린다. 화면이 실제 상태와 어긋나면 안 된다.
        settings = self.lan_values() if wanted == "lan" else {}
        if self.on_mode(wanted, settings):
            self.apply_mode(wanted)
            self.probe_result.SetLabel("적용되었습니다. 시작할 때 연결합니다." if wanted == "lan"
                                       else "시험하지 않았습니다.")
            self.probe_result.SetForegroundColour(MUTED)
        else:
            self.mode.SetSelection(self.MODES.index("lan" if wanted == "nmea" else "nmea"))
        self.Layout()

    def apply_address(self, event):
        """LAN 모드에서 고친 주소를 즉시 반영한다.

        모드 전환과 같은 on_mode 경로를 쓴다. 거부되면(실험 창이 열려 있음) 이유를
        보여주고 값을 바꾸지 않는다.
        """
        if self.on_mode is None:
            return
        values = self.lan_values()
        if self.on_mode("lan", values):
            self.probe_result.SetLabel(f"적용되었습니다 · {values['host']}:{values['port']} · 시작할 때 연결합니다.")
            self.probe_result.SetForegroundColour(MUTED)
        else:
            self.probe_result.SetLabel("실험 창이 열려 있어 주소를 바꿀 수 없습니다. 실험을 닫고 다시 적용하세요.")
            self.probe_result.SetForegroundColour(DANGER)
        self.Layout()

    def show_save_error(self, message):
        """주소는 적용됐지만 다음 실행까지 남기지 못한 경우를 알린다."""
        if message:
            self.probe_result.SetLabel(message)
            self.probe_result.SetForegroundColour(DANGER)
            self.Layout()

    def lan_values(self):
        try:
            port = int(self.port.GetValue().strip())
        except ValueError:
            port = hiedge.DEFAULT_PORT
            self.port.SetValue(str(port))
        return {"host": self.host.GetValue().strip() or hiedge.DEFAULT_HOST,
                "port": port, "verify": not self.skip_verify.GetValue()}

    # --- 연결 시험 ---

    def run_probe(self):
        """장비에 한 번 접속해 첫 표본까지 확인한다.

        소켓 I/O는 GUI 스레드를 멈추므로 작업 스레드에서 돌리고, 결과는
        wx.CallAfter로 돌려받는다.
        """
        if self.on_probe is None:
            return
        self.probe_button.Enable(False)
        self.probe_result.SetForegroundColour(MUTED)
        self.probe_result.SetLabel("연결 중…")
        self.Layout()
        self.on_probe(self.lan_values(), self.show_probe_result)

    def show_probe_result(self, ok, message):
        """작업 스레드가 끝난 뒤 GUI 스레드에서 호출된다."""
        if not self:
            return
        self.probe_button.Enable(self.MODES[self.mode.GetSelection()] == "lan")
        self.probe_result.SetForegroundColour(MUTED if ok else DANGER)
        self.probe_result.SetLabel(message)
        self.probe_result.Wrap(self.GetClientSize().width - self.FromDIP(48))
        self.Layout()
        parent = self.GetParent()
        if hasattr(parent, "FitInside"):
            parent.FitInside()

    def select_nmea(self, on_nmea):
        with wx.FileDialog(self, "NMEA 파일 선택", wildcard="NMEA 파일 (*.nmea)|*.nmea|모든 파일|*.*",
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialog:
            if dialog.ShowModal() == wx.ID_OK and on_nmea(dialog.GetPath()):
                self.file_label.SetLabel(Path(dialog.GetPath()).name)
                self.file_label.SetToolTip(dialog.GetPath())
                self.Layout()
