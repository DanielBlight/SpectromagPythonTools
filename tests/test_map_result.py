"""Offline result tests. Fake sessions are test fixtures, not measurement data."""

import csv
import importlib.util
import json
from pathlib import Path

import pytest
from test_keithley2600 import make

from spectromagpythontools.routines.map_result import load_map_result, scan_map_result


def write_map(path, *, partial=False, rows=None, xs=None, ys=None):
    xs = [3, 2, 1] if xs is None else xs
    ys = [5, 4] if ys is None else ys
    if rows is None:
        rows = [
            (r, c, xs[c], ys[r], 10 * r + c, "[]")
            for r in range(len(ys))
            for c in (range(len(xs)) if r % 2 == 0 else reversed(range(len(xs))))
        ]
    with path.open("w", newline="", encoding="utf-8") as f:
        f.write("# " + json.dumps({"xs": xs, "ys": ys}) + "\n")
        writer = csv.writer(f)
        writer.writerow(
            [
                "row",
                "column",
                "requested_x",
                "requested_y",
                "mean_current_a",
                "readings_json",
            ]
        )
        writer.writerows(rows)
        f.write(
            "# "
            + json.dumps(
                {"status": "partial" if partial else "complete", "pixels": len(rows)}
            )
            + "\n"
        )


def test_serpentine_descending_axes(tmp_path):
    np = pytest.importorskip("numpy")
    path = tmp_path / "map.csv"
    write_map(path)
    result = load_map_result(path)
    assert result.complete
    assert result.xs == (3, 2, 1) and result.ys == (5, 4)
    np.testing.assert_array_equal(result.to_numpy(), [[0, 1, 2], [10, 11, 12]])


def test_dataframe_matches_csv(tmp_path):
    pd = pytest.importorskip("pandas")
    path = tmp_path / "map.csv"
    write_map(path)
    result = load_map_result(path)
    actual = result.to_dataframe()
    pd.testing.assert_frame_equal(
        actual, pd.read_csv(path, comment="#"), check_dtype=False
    )
    assert actual.attrs["metadata"]["xs"] == [3, 2, 1]
    assert actual["column"].tolist() == [0, 1, 2, 2, 1, 0]


def test_partial_nan_and_default_rejection(tmp_path):
    np = pytest.importorskip("numpy")
    path = tmp_path / "map.csv"
    write_map(path, partial=True, rows=[(0, 0, 3, 5, 0, "[]")])
    with pytest.raises(ValueError, match="partial"):
        load_map_result(path)
    result = load_map_result(path, allow_partial=True)
    assert not result.complete
    assert result.to_numpy()[0, 0] == 0
    assert np.isnan(result.to_numpy()[1, 2])


@pytest.mark.parametrize(
    "rows, message",
    [
        ([(0, 0, 3, 5, 1, "[]")] * 2, "Duplicate pixel"),
        ([(9, 0, 3, 5, 1, "[]")], "outside"),
        ([(0, 0, 999, 5, 1, "[]")], "coordinates"),
        ([(0, 0, 3, 5, float("nan"), "[]")], "Invalid mean"),
        ([(0, 0, 3, 5, 1, "[]")], "Completion footer"),
    ],
)
def test_bad_maps(tmp_path, rows, message):
    path = tmp_path / "map.csv"
    write_map(path, rows=rows)
    with pytest.raises(ValueError, match=message):
        load_map_result(path)


@pytest.mark.parametrize("xs,ys", [([0], [0]), ([0, 1], [0]), ([0], [0, 1])])
def test_small_shapes(tmp_path, xs, ys):
    pytest.importorskip("numpy")
    path = tmp_path / "map.csv"
    write_map(path, xs=xs, ys=ys)
    assert load_map_result(path).to_numpy().shape == (len(ys), len(xs))


def test_existing_scan_engine_integration(tmp_path):
    smu, session = make()
    path = tmp_path / "map.csv"
    result = scan_map_result(
        smu,
        xs=[0, 1],
        ys=[0, 1],
        move_and_wait=lambda x, y: (x, y),
        output_csv=path,
        samples=3,
        settle_s=0,
        position_unit="mrad",
    )
    assert len(result.records) == 4 and result.complete
    assert result.metadata["samples"] == 3
    assert len(json.loads(result.records[0]["readings_json"])) == 3
    assert session.v["source.output"] == 0
    with pytest.raises(FileExistsError):
        scan_map_result(
            smu,
            xs=[0],
            ys=[0],
            move_and_wait=lambda x, y: (x, y),
            output_csv=path,
            settle_s=0,
        )


def test_failure_preserves_partial_csv_and_disables_output(tmp_path):
    smu, session = make()
    path = tmp_path / "map.csv"

    def move(x, y):
        if x == 1:
            raise RuntimeError("motion failed")
        return x, y

    with pytest.raises(RuntimeError, match="motion failed"):
        scan_map_result(
            smu, xs=[0, 1], ys=[0], move_and_wait=move, output_csv=path, settle_s=0
        )
    result = load_map_result(path, allow_partial=True)
    assert len(result.records) == 1 and not result.complete
    assert session.v["source.output"] == 0


@pytest.mark.parametrize(
    "name", ["keithley_measurement", "mirror_moves", "repeatable_map"]
)
def test_example_import_is_inert(name):
    path = Path(__file__).resolve().parents[1] / "examples" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.main)


@pytest.mark.parametrize("fail", [False, True])
def test_repeatable_example_runs_with_fake_devices(tmp_path, monkeypatch, fail):
    import os
    from contextlib import nullcontext
    from dataclasses import asdict
    from types import SimpleNamespace

    path = Path(__file__).resolve().parents[1] / "examples" / "repeatable_map.py"
    spec = importlib.util.spec_from_file_location("repeatable_example_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    smu, session = make()
    config = {
        "keithley": {},
        "pgm1se": {},
        "photocurrent_map": {
            **asdict(smu.settings),
            "max_abs_bias_v": 1,
            "max_current_a": 1e-5,
        },
    }
    prepared = []
    mirror = SimpleNamespace(prepare_closed_loop=lambda: prepared.append(True))
    monkeypatch.setattr(module, "load_config", lambda path: config)
    monkeypatch.setattr(module, "build_mount", lambda section: nullcontext(mirror))
    monkeypatch.setattr(module, "build_smu", lambda section, **kw: nullcontext(smu))
    monkeypatch.setattr(module, "mount_metadata", lambda mount: {"test_fixture": True})

    def mover(mount, **kw):
        def move(x, y):
            assert prepared and os.environ["SPECTROMAG_MIRROR_POLICY"] == "verified"
            if fail:
                raise RuntimeError("test motion failure")
            return x, y

        return move

    monkeypatch.setattr(module, "gimbal_mover", mover)
    monkeypatch.setenv("SPECTROMAG_MIRROR_POLICY", "one_check")
    if fail:
        with pytest.raises(RuntimeError, match="test motion failure"):
            module.acquire(
                "unused.toml", tmp_path / "map.csv", xs=[0], ys=[0], settle_s=0
            )
    else:
        result = module.acquire(
            "unused.toml", tmp_path / "map.csv", xs=[0], ys=[0], settle_s=0
        )
        assert result.complete and result.metadata["samples"] == 3
    assert os.environ["SPECTROMAG_MIRROR_POLICY"] == "one_check"
    assert session.v["source.output"] == 0
