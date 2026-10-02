import wx
from experiment_app.ui.components.wheel_scrolled_panel import WheelScrolledPanel
from experiment_app.domain.session import State, ACTIVE, TERMINAL, LABELS
from experiment_app.domain.test_definition import AppError
from experiment_app.ui.theme import style, text, CONCRETE, MUTED
from experiment_app.ui.components.header import Header
from experiment_app.ui.panes.sensor_part import SensorPartPane
from experiment_app.ui.panes.gps_map import GpsMapPane
from experiment_app.ui.panes.result_charts import ResultChartsPane

TONES = {State.STARTING: "active", State.RUNNING: "active", State.STOPPING: "active",
         State.COMPLETED: "ok", State.ERROR: "error"}


class ExperimentFrame(wx.Frame):
    def __init__(self, parent, presenter, tiles, on_closed):
        super().__init__(parent, title="MECHLab 실험 (데모)")
        if wx.Platform == "__WXMSW__":
            self.SetDoubleBuffered(True)
        self.presenter = presenter
        self.on_closed = on_closed
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
            ("charts", "결과 그래프", self.toggle_charts, False),
            ("cleanup", "종료 정리 재시도", self.presenter.sessions.retry_cleanup, False), None,
            ("main", "메인 보기", lambda: parent.Raise(), False),
        ))
        root.Add(self.header, 0, wx.EXPAND)
        strip = wx.BoxSizer(wx.HORIZONTAL)
        strip.Add(text(self, "로봇", 13, colour=MUTED, weight="semibold"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, self.FromDIP(12))
        self.robot_status = text(self, presenter.robot_status(), 13)
        strip.Add(self.robot_status, 1, wx.ALIGN_CENTER_VERTICAL)
        root.Add(strip, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, self.FromDIP(16))
        self.body = WheelScrolledPanel(self)
        self.body.SetBackgroundColour(CONCRETE)
        self.body_sizer = wx.BoxSizer(wx.VERTICAL)
        self.sensors = {part: SensorPartPane(self.body, part, presenter.sessions.channels) for part in "BC"}
        self.map = GpsMapPane(self.body, tiles)
        self.body_sizer.Add(self.map, 6, wx.EXPAND | wx.ALL, self.FromDIP(10))
        for pane in self.sensors.values():
            self.body_sizer.Add(pane, 2, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, self.FromDIP(4))
        # 결과 그래프는 같은 본문 자리에 교체해 넣는다. 창을 새로 띄우지 않는다.
        self.charts = ResultChartsPane(self.body)
        self.charts.Hide()
        self.body_sizer.Add(self.charts, 1, wx.EXPAND)
        self.showing_charts = False
        self.body.SetSizer(self.body_sizer)
        self.body.SetupScrolling(scroll_x=False, rate_y=16)
        self.bind_body_scroll()
        root.Add(self.body.with_scrollbar(), 1, wx.EXPAND)
        self.SetSizer(root)
        self.CreateStatusBar()
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.tick, self.timer)
        self.Bind(wx.EVT_CLOSE, self.close)
        self.Bind(wx.EVT_SIZE, self.resize)
        self.timer.Start(100)
        self.tick()

    def bind_body_scroll(self):
        """본문 스크롤 입력을 다시 연결한다.

        제스처를 스스로 쓰는 것은 지도 그림판과 그래프 그림판뿐이다. 그 둘을
        담은 pane 전체를 빼면 지도 머리줄 버튼·좌표, 그래프 제목·범례·'전체 보기'
        위에서는 손가락이 전혀 먹지 않는다. 그림판만 뺀다.
        """
        charts = (self.charts.speed.chart, self.charts.deviation.chart)
        self.body.bind_wheel_children(exclude=(self.map.map,) + charts)
        for chart in charts:
            # 확대 전 그래프는 끌어도 움직일 수 없다. 그 끌기는 본문 스크롤로 쓴다.
            chart.on_spare_drag = self.scroll_body_by

    def scroll_body_by(self, dy):
        """그림판이 쓰지 않은 세로 끌기만큼 본문을 움직인다."""
        self.body.scroll_by_pixels(-dy)

    def start(self):
        self.scenario = self.presenter.sessions.scenario
        self.presenter.sessions.start(self.presenter.sessions.view().session_id)
        self.tick()

    def stop(self):
        self.presenter.sessions.stop(self.presenter.sessions.view().session_id)
        self.tick()

    def toggle_charts(self):
        """본문을 측정 화면과 결과 그래프 사이에서 바꾼다.

        같은 창의 같은 자리를 교체한다. 그래프는 기록 파일을 읽어 그리므로 측정 중에는
        쓸 수 없고(기록이 아직 닫히지 않았다), tick()이 버튼을 비활성으로 둔다.
        """
        if self.showing_charts:
            self.show_charts(False)
            return
        session = self.presenter.sessions.view()
        try:
            analysis = self.presenter.analyse(session.session_id)
        except AppError as error:
            self.SetStatusText(f"결과 그래프를 그릴 수 없습니다: {error}")
            return
        self.charts.render(analysis)
        self.show_charts(True)
        self.SetStatusText("결과 그래프입니다. 같은 버튼을 다시 누르면 측정 화면으로 돌아갑니다.")

    def show_charts(self, showing):
        self.showing_charts = showing
        self.map.Show(not showing)
        for pane in self.sensors.values():
            pane.Show(not showing)
        self.charts.Show(showing)
        self.header.buttons["charts"].SetLabel("측정 화면" if showing else "결과 그래프")
        # 범례는 render마다 새 위젯으로 다시 만든다. 새 자식에 스크롤 입력을 연결한다.
        self.bind_body_scroll()
        self.body.Layout()
        self.body.FitInside()
        self.Layout()

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
        # 그래프는 기록 파일을 읽어 그린다. 기록이 닫히기 전에는 자료가 온전하지 않으므로
        # 측정이 끝나고 정리까지 마친 뒤에만 켠다.
        finished = session.state in TERMINAL and self.presenter.sessions.is_idle()
        self.header.buttons["charts"].Enable(finished or self.showing_charts)
        if self.showing_charts and not finished:
            # 재실행 등으로 측정이 다시 시작되면 낡은 그래프를 남기지 않는다.
            self.show_charts(False)
        self.header.buttons["cleanup"].Show(session.state == State.ERROR and not session.cleaned_up)
        snapshot = self.presenter.telemetry.snapshot()
        if not self.showing_charts:
            # 그래프를 보는 동안에는 숨은 위젯을 갱신하지 않는다.
            models = self.presenter.metrics(snapshot)
            for pane in self.sensors.values():
                pane.render(models)
            self.map.render(snapshot, self.scenario == "지도 배경 실패", live=session.state in ACTIVE)
        if self.showing_charts:
            # 그래프 화면에서는 상태바가 그래프 안내를 유지한다.
            pass
        elif session.state in TERMINAL:
            self.SetStatusText(f"{LABELS[session.state]}: {session.end_reason}   세션 {session.session_id[:8]}")
        elif session.state == State.RUNNING:
            # 끝이 있는 재생 소스와 끝이 없는 실시간 소스를 구분해 사실만 적는다.
            source = self.presenter.sessions.sensors
            if getattr(source, "continuous", False):
                detail = "중지를 누를 때까지 계속 기록합니다."
            else:
                total = getattr(source, "duration", None)
                detail = ("자료를 끝까지 재생하면 자동으로 끝납니다."
                          if total is None else
                          f"자료 {total:g}초를 끝까지 재생하면 자동으로 끝납니다.")
            self.SetStatusText(f"기록 중입니다. {detail}   세션 {session.session_id[:8]}")
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
        self.charts.dispose()
        for pane in self.sensors.values():
            pane.dispose()
        self.presenter.sessions.release_ready()
        self.on_closed()
