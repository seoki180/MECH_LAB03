"""결과 그래프의 줌 범위 계산 (GUI 이벤트와 분리)."""
import pytest

from experiment_app.ui.adapters.chart import ChartViewport


def test_zoom_keeps_pointer_position_on_both_axes():
    viewport = ChartViewport((0.0, 100.0, -50.0, 50.0))
    viewport.zoom(2.0, 0.25, 0.75)
    assert viewport.bounds == pytest.approx((12.5, 62.5, -37.5, 12.5))


def test_zoom_is_clamped_to_data_and_can_reset():
    viewport = ChartViewport((0.0, 100.0, 0.0, 100.0))
    viewport.zoom(2.0, 0.0, 1.0)
    assert viewport.bounds == pytest.approx((0.0, 50.0, 0.0, 50.0))
    viewport.zoom(0.01, 0.5, 0.5)
    assert viewport.bounds == (0.0, 100.0, 0.0, 100.0)
    viewport.zoom(10000.0, 0.5, 0.5)
    assert viewport.bounds[1] - viewport.bounds[0] == pytest.approx(100 / 64)
    viewport.reset()
    assert viewport.bounds == (0.0, 100.0, 0.0, 100.0)


def test_pinch_uses_gesture_start_bounds_instead_of_multiplying_updates():
    viewport = ChartViewport((0.0, 100.0, 0.0, 100.0))
    start = viewport.bounds
    viewport.zoom(2.0, 0.5, 0.5, base=start)
    viewport.zoom(3.0, 0.5, 0.5, base=start)
    assert viewport.bounds[1] - viewport.bounds[0] == pytest.approx(100 / 3)


def test_empty_chart_cannot_zoom():
    viewport = ChartViewport(None)
    viewport.zoom(2.0, 0.5, 0.5)
    assert viewport.bounds is None
