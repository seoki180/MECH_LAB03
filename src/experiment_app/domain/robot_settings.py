"""Robot input schemas; no inferred device limits or defaults."""
from .test_definition import FieldSchema


def field(key, label, unit="", kind="float", help="로봇 설정 · 미설정 허용 · 장비 제한 미확정"):
    return FieldSchema(key, label, kind=kind, unit=unit, required=False, help=help)


ROBOT_FIELDS = {
    "ar_trapezoidal_step": (
        field("control", "Control", kind="str", help="직접 입력 · 이미지 참고: Position"),
        field("use_br_for_speed_control", "Use BR for speed control", kind="bool"),
        field("use_gear_robot", "Use gear robot", kind="bool"),
        field("gear_mode", "Gear mode", kind="str", help="직접 입력 · 이미지 참고: Auto Select"),
        field("start_delay", "Start delay", "s"),
        field("start_level", "Start level", "%"),
        field("apply_rate", "Apply rate", "%/s"),
        field("amplitude", "Amplitude", "%"),
        field("dwell_time", "Dwell time", "s"),
        field("return_rate", "Return rate", "%/s"),
        field("end_delay", "End delay", "s"),
        field("no_of_cycles", "No of cycles", kind="int"),
    ),
    "pf_straight_line": (
        field("control", "Control", kind="str", help="직접 입력 · 이미지 참고: Robot steering"),
        field("start_x", "Start X", "m"), field("start_y", "Start Y", "m"),
        field("distance", "Distance", "m"), field("angle", "Angle", "°"),
        field("end_x", "End X", "m"), field("end_y", "End Y", "m"),
        field("join_anywhere", "Join anywhere", kind="bool"),
        field("allow_auto_heading_reversal", "Allow auto heading reversal", kind="bool"),
        field("maximum_steering_wheel_amplitude", "Maximum steering wheel amplitude", "°"),
        field("maximum_steering_wheel_velocity", "Maximum steering wheel velocity", "°/s"),
        field("maximum_steering_wheel_acceleration", "Maximum steering wheel acceleration", "°/s²"),
    ),
}
