import wx
from wx.lib.scrolledpanel import ScrolledPanel
from experiment_app.domain.session import State, ACTIVE, TERMINAL, LABELS
from experiment_app.ui.theme import style, text, CONCRETE, MUTED
from experiment_app.ui.components.header import Header
from experiment_app.ui.panes.sensor_part import SensorPartPane
from experiment_app.ui.panes.gps_map import GpsMapPane

TONES = {State.STARTING: "active", State.RUNNING: "active", State.STOPPING: "active",
         State.COMPLETED: "ok", State.ERROR: "error"}


class ExperimentFrame(wx.Frame):
    def __init__(self, parent, presenter, clipboard, tiles, on_results, on_closed):
        super().__init__(parent, title="MECHLab 실험 (데모)")
        self.presenter, self.clipboard = presenter, clipboard
        self.on_results, self.on_closed = on_results, on_closed
        self.closing, self.disposed = False, False
        self.scenario = presenter.sessions.scenario
        style(self)
        self.SetClientSize(self.FromDIP((1280, 800)))
        self.SetMinSize(self.FromDIP((600, 500)))
        root = wx.BoxSizer(wx.VERTICAL)
        session = presenter.sessions.view()
        definition = session.snapshot.definition
        self.header = Header(self, f"{definition.name} / r{definition.revision}", (
            ("start", "시작", self.start, True), ("stop", "중지", self.stop, "danger"), None,
            ("copy", "데이터 복사 ▾", self.copy_menu, False),
            ("results", "결과 보기", lambda: on_results(session.session_id), False),
            ("cleanup", "종료 정리 재시도", self.presenter.sessions.retry_cleanup, False), None,
            ("main", "메인 보기", lambda: parent.Raise(), False),
        ))
        root.Add(self.header, 0, wx.EXPAND)
        strip = wx.BoxSizer(wx.HORIZONTAL)
        strip.Add(text(self, "로봇", 13, colour=MUTED, weight="semibold"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, self.FromDIP(12))
        self.robot_status = text(self, presenter.robot_status(), 13)
        strip.Add(self.robot_status, 1, wx.ALIGN_CENTER_VERTICAL)
        root.Add(strip, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, self.FromDIP(16))
        self.body = ScrolledPanel(self)
        self.body.SetBackgroundColour(CONCRETE)
        self.body_sizer = wx.BoxSizer(wx.VERTICAL)
        self.sensors = {part: SensorPartPane(self.body, part, presenter.sessions.channels) for part in "BC"}
        self.map = GpsMapPane(self.body, tiles)
        self.body_sizer.Add(self.map, 6, wx.EXPAND | wx.ALL, self.FromDIP(10))
        for pane in self.sensors.values():
            self.body_sizer.Add(pane, 2, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, self.FromDIP(4))
        self.body.SetSizer(self.body_sizer)
        self.body.SetupScrolling(scroll_x=False, rate_y=16)
        root.Add(self.body, 1, wx.EXPAND)
        self.SetSizer(root)
        self.CreateStatusBar()
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.tick, self.timer)
        self.Bind(wx.EVT_CLOSE, self.close)
        self.Bind(wx.EVT_SIZE, self.resize)
        self.timer.Start(100)
        self.tick()

    def start(self):
        self.scenario = self.presenter.sessions.scenario
        self.presenter.sessions.start(self.presenter.sessions.view().session_id)
        self.tick()

    def stop(self):
        self.presenter.sessions.stop(self.presenter.sessions.view().session_id)
        self.tick()

    def copy_menu(self):
        menu = wx.Menu()
        for part, label in ((None, "현재 화면 채널 전체"), ("B", "동쪽·북쪽 이동"), ("C", "현재 속도·누적 이동거리")):
            item = menu.Append(wx.ID_ANY, label)
            menu.Bind(wx.EVT_MENU, lambda e, p=part: self.copy(p), item)
        self.PopupMenu(menu)
        menu.Destroy()

    def copy(self, part=None):
        content = self.presenter.copy(part)
        if not content:
            self.SetStatusText("복사할 측정 데이터가 아직 없습니다.")
        elif self.clipboard.set_text(content):
            self.SetStatusText("최신 화면 채널과 GPS를 TSV로 복사했습니다.")
        else:
            wx.MessageBox("클립보드를 사용할 수 없습니다. 데이터 복사를 다시 눌러 재시도하세요.", "클립보드 사용 불가", parent=self)

    def tick(self, event=None):
        if self.disposed:
            return
        session = self.presenter.sessions.view()
        status = self.presenter.robot_status()
        if self.robot_status.GetLabel() != status:
            self.robot_status.SetLabel(status)
            self.robot_status.Wrap(max(200, self.GetClientSize().width - self.FromDIP(96)))
            self.Layout()
        self.header.render(LABELS[session.state], TONES.get(session.state, "idle"),
                           f"{int(session.elapsed)//60:02}:{int(session.elapsed)%60:02}")
        self.header.buttons["start"].Enable(session.state == State.READY)
        self.header.buttons["stop"].Enable(session.state in {State.STARTING, State.RUNNING})
        self.header.buttons["results"].Enable(session.state in TERMINAL and self.presenter.sessions.is_idle())
        self.header.buttons["cleanup"].Show(session.state == State.ERROR and not session.cleaned_up)
        snapshot = self.presenter.telemetry.snapshot()
        self.header.buttons["copy"].Enable(bool(snapshot.samples or snapshot.gps))
        models = self.presenter.metrics(snapshot)
        for pane in self.sensors.values():
            pane.render(models)
        self.map.render(snapshot, self.scenario == "지도 배경 실패", live=session.state in ACTIVE)
        if session.state in TERMINAL:
            self.SetStatusText(f"{LABELS[session.state]}: {session.end_reason}   세션 {session.session_id[:8]}")
        elif session.state == State.RUNNING:
            self.SetStatusText(f"NMEA 파일을 재생하며 기록 중입니다. 최대 30초 후 자동으로 끝납니다.   세션 {session.session_id[:8]}")
        elif session.state == State.STOPPING:
            self.SetStatusText("수집을 멈추고 기록을 정리하는 중입니다…")
        else:
            self.SetStatusText(f"시작을 누르면 로봇에 연결해 설정을 보냅니다.   세션 {session.session_id[:8]}")
        if self.closing and self.presenter.sessions.is_idle():
            self.dispose()
            self.Destroy()

    def resize(self, event):
        if hasattr(self, "sensors"):
            portrait = self.ToDIP(self.GetClientSize()).width < 800
            for pane in self.sensors.values():
                pane.set_stacked(portrait)
            self.Layout()
            self.body.FitInside()
        event.Skip()

    def request_close(self, confirm=True):
        session = self.presenter.sessions.view()
        if session.state in ACTIVE or not self.presenter.sessions.is_idle():
            if confirm and session.state in {State.STARTING, State.RUNNING}:
                dialog = wx.MessageDialog(self, "측정을 중지하고 기록 정리가 끝난 뒤 창을 닫습니다.", "실험 종료", wx.YES_NO | wx.NO_DEFAULT)
                dialog.SetYesNoLabels("측정 중지 후 닫기", "취소")
                result = dialog.ShowModal()
                dialog.Destroy()
                if result != wx.ID_YES:
                    return False
            self.closing = True
            self.stop()
            return True
        self.dispose()
        self.Destroy()
        return True

    def close(self, event):
        if event.CanVeto():
            event.Veto()
        self.request_close()

    def dispose(self):
        if self.disposed:
            return
        self.disposed = True
        self.timer.Stop()
        self.map.dispose()
        for pane in self.sensors.values():
            pane.dispose()
        self.presenter.sessions.release_ready()
        self.on_closed()
