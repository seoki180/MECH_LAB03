import wx
from experiment_app.ui.components.wheel_scrolled_panel import WheelScrolledPanel
from experiment_app.ui.theme import style, button, add, scaled
from experiment_app.ui.pages.devices_page import DevicesPage


class SettingsDialog(wx.Dialog):
    def __init__(self, parent, scenarios, on_scenario, on_nmea, nmea_path, scenario,
                 on_mode=None, on_probe=None, mode="nmea", lan_settings=None,
                 on_robot=None, robot_settings=None, robot_status="", on_robot_probe=None):
        super().__init__(parent, title="장치 설정", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        style(self)
        self.SetClientSize(self.FromDIP((scaled(620), scaled(620))))
        self.SetMinSize(self.FromDIP((scaled(420), scaled(360))))
        root = wx.BoxSizer(wx.VERTICAL)
        body = WheelScrolledPanel(self)
        style(body)
        self.devices = DevicesPage(body, scenarios, on_scenario, on_nmea, nmea_path,
                                   on_mode, on_probe, mode, lan_settings,
                                   on_robot, robot_settings, robot_status, on_robot_probe)
        self.devices.scenario.SetStringSelection(scenario)
        content = wx.BoxSizer(wx.VERTICAL)
        content.Add(self.devices, 1, wx.EXPAND)
        body.SetSizer(content)
        body.SetupScrolling(scroll_x=False, rate_y=16)
        body.bind_wheel_children()
        root.Add(body, 1, wx.EXPAND)
        add(root, button(self, "닫기", lambda: self.EndModal(wx.ID_CLOSE)), border=12)
        self.SetSizer(root)
        self.CentreOnParent()
