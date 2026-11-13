"""Offline viewer/export tests. All CSV fixtures are synthetic."""

import json
from types import SimpleNamespace

import pytest
from matplotlib.backends.backend_agg import FigureCanvasAgg

from spectromagpythontools.gui.map_files import pixel_at, read_map, save_png
from spectromagpythontools.gui.map_plot import MapFigure, MapPlot


def fixture_csv(tmp_path, *, rows=None, footer=True):
    path = tmp_path / "synthetic.csv"
    metadata = {"xs": [1, 0], "ys": [-1, 1], "position_unit": "mrad"}
    rows = rows if rows is not None else ["0,0,1,-1,2e-9", "1,1,0,1,3e-9"]
    text = (
        "# "
        + json.dumps(metadata)
        + "\nrow,column,requested_x,requested_y,mean_current_a\n"
        + "\n".join(rows)
        + "\n"
    )
    if footer:
        text += "# " + json.dumps({"status": "partial"}) + "\n"
    path.write_text(text)
    return path


def test_partial_csv_load_and_descending_axes(tmp_path):
    xs, ys, cells, status = read_map(fixture_csv(tmp_path))
    assert xs == (1, 0) and ys == (-1, 1)
    assert cells == {(0, 0): 2e-9, (1, 1): 3e-9}
    assert status == "partial"


def test_missing_footer_not_complete(tmp_path):
    assert "incomplete" in read_map(fixture_csv(tmp_path, footer=False))[3]


@pytest.mark.parametrize(
    "rows",
    [
        ["0,0,1,-1,nan"],
        ["2,0,1,-1,1e-9"],
        ["0,0,1,-1,1e-9", "0,0,1,-1,2e-9"],
        ["0,0,0,-1,1e-9"],
        ["bad,0,1,-1,1e-9"],
    ],
)
def test_invalid_rows_rejected(tmp_path, rows):
    with pytest.raises(ValueError):
        read_map(fixture_csv(tmp_path, rows=rows))


def test_empty_partial_csv_retains_grid(tmp_path):
    assert read_map(fixture_csv(tmp_path, rows=[]))[2] == {}


def test_wrong_units_rejected(tmp_path):
    path = fixture_csv(tmp_path)
    path.write_text(path.read_text().replace("mrad", "um"))
    with pytest.raises(ValueError, match="mrad"):
        read_map(path)


@pytest.mark.parametrize(
    "xs,ys,x,y,expected",
    [
        ((0, 1), (0, 1), 0, 1, (1, 0)),
        ((1, 0), (1, 0), 0, 1, (0, 1)),
        ((0,), (0,), 0, 0, (0, 0)),
        ((0, 1), (0, 1), 2, 0, None),
        ((0, 1), (0, 1), None, 0, None),
        ((0, 1), (0, 1), float("nan"), 0, None),
    ],
)
def test_hover_index(xs, ys, x, y, expected):
    assert pixel_at(xs, ys, x, y) == expected


def test_png_real_render_and_collision(tmp_path):
    plot = MapFigure()
    plot.update((0, 1), (0, 1), {(0, 0): 2e-9})
    FigureCanvasAgg(plot.figure)
    path = tmp_path / "map.png"
    save_png(plot.figure, path)
    assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        save_png(plot.figure, path)
    assert path.read_bytes() == before


def test_failed_export_removes_only_new_file(tmp_path):
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic rendering failure")

    path = tmp_path / "failed.png"
    with pytest.raises(RuntimeError):
        save_png(SimpleNamespace(savefig=fail), path)
    assert not path.exists()


def test_hover_readout_and_clear():
    value = []
    axes = object()
    obj = SimpleNamespace(
        last=((0, 1), (0, 1), {(0, 1): 2e-9}),
        model=SimpleNamespace(axes=axes),
        hover_text=SimpleNamespace(set=value.append),
    )
    MapPlot.hover(obj, SimpleNamespace(inaxes=axes, xdata=1, ydata=0))
    assert "X=1 mrad" in value[-1] and "Row 1, column 2" in value[-1]
    assert "2e-09 A" in value[-1]
    MapPlot.hover(obj, SimpleNamespace(inaxes=axes, xdata=0, ydata=1))
    assert "not acquired" in value[-1]
    MapPlot.hover(obj, SimpleNamespace(inaxes=None, xdata=None, ydata=None))
    assert value[-1] == ""


def test_load_csv_callback_no_hardware_and_preserves_acquisition_fields(tmp_path):
    from spectromagpythontools.gui.operator import Application

    path = fixture_csv(tmp_path)
    messages = []
    app = SimpleNamespace(
        busy=False,
        closing=False,
        dialog=SimpleNamespace(askopenfilename=lambda **kw: str(path)),
        progress=SimpleNamespace(configure=lambda **kw: None),
        progress_text=SimpleNamespace(set=messages.append),
        draw_map=lambda: messages.append("draw"),
        log=messages.append,
        messagebox=SimpleNamespace(showerror=lambda *a: pytest.fail(str(a))),
        values={"bias_v": "unchanged"},
    )
    Application.load_map_csv(app)
    assert app.cells[(0, 0)] == 2e-9 and app.grid_axes == ((1, 0), (-1, 1))
    assert app.values == {"bias_v": "unchanged"}
    assert any("partial" in message for message in messages)


def test_load_csv_busy_refused_before_dialog():
    from spectromagpythontools.gui.operator import Application

    errors = []
    app = SimpleNamespace(
        busy=True,
        closing=False,
        messagebox=SimpleNamespace(showerror=lambda *a: errors.append(a)),
    )
    Application.load_map_csv(app)
    assert errors


def test_auto_png_only_when_checked(tmp_path):
    from spectromagpythontools.gui.operator import Application

    saved = []
    app = SimpleNamespace(
        save_png_enabled=SimpleNamespace(get=lambda: False),
        draw_map=lambda: None,
        _export_map_png=saved.append,
    )
    Application.map_completed(app, {"output_csv": str(tmp_path / "map.csv")})
    assert not saved
    app.save_png_enabled.get = lambda: True
    Application.map_completed(app, {"output_csv": str(tmp_path / "map.csv")})
    assert saved == [tmp_path / "map.png"]
