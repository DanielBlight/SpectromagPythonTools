"""Layer boundaries, backward compatibility and behaviour-preserving extraction."""

import ast
import hashlib
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "spectromagpythontools"
CONTRACTS = json.loads(
    (ROOT / "tests/fixtures/architecture_contracts.json").read_text()
)


@pytest.mark.parametrize("old,new", CONTRACTS["aliases"].items())
def test_legacy_module_is_same_implementation(old, new):
    assert importlib.import_module(
        "spectromagpythontools." + old
    ) is importlib.import_module("spectromagpythontools." + new)


@pytest.mark.parametrize(
    "layer,forbidden",
    [
        (
            "devices",
            {
                "routines",
                "application",
                "gui",
                "cli",
                "scripts",
                "data",
                "visualization",
            },
        ),
        (
            "diagnostics",
            {"devices", "routines", "application", "gui", "cli", "scripts"},
        ),
        ("data", {"devices", "routines", "application", "gui", "cli", "scripts"}),
        (
            "visualization",
            {"devices", "routines", "application", "gui", "cli", "scripts"},
        ),
        ("application", {"gui", "cli", "scripts"}),
    ],
)
def test_layer_dependencies(layer, forbidden):
    for path in (PACKAGE / layer).rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [n.name for n in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            for name in names:
                if name.startswith("spectromagpythontools."):
                    assert name.split(".")[1] not in forbidden, (path, name)
                assert name.split(".")[0] != "tkinter", (path, name)


def test_production_does_not_import_legacy_aliases():
    legacy = {"spectromagpythontools." + name for name in CONTRACTS["aliases"]}
    for path in PACKAGE.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert node.module not in legacy, (path, node.module)


@pytest.mark.parametrize("key,digest", CONTRACTS["classes"].items())
def test_extracted_device_class_ast_unchanged(key, digest):
    module, name = key.split(":")
    path = PACKAGE / (module.replace(".", "/") + ".py")
    node = next(
        n
        for n in ast.parse(path.read_text()).body
        if isinstance(n, ast.ClassDef) and n.name == name
    )
    assert (
        hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()
        == digest
    )


@pytest.mark.parametrize(
    "module",
    [
        "cli.commissioning_gui",
        "cli.fit_pgm1se_conversion",
        "cli.inspect_pgm1se",
        "cli.map_timing",
        "scripts.commissioning_gui",
        "scripts.fit_pgm1se_conversion",
        "scripts.inspect_pgm1se",
        "scripts.map_timing",
    ],
)
def test_cli_help_is_inert(module):
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, "-m", "spectromagpythontools." + module, "--help"],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


def test_cold_import_without_hardware_or_tk():
    code = """
import sys
sys.modules['tkinter'] = None
sys.modules['pyvisa'] = None
from spectromagpythontools.application.control import Session, HardwareWorker
from spectromagpythontools.application.operator_control import OperatorSession
from spectromagpythontools.data.map_result import MapResult
from spectromagpythontools.visualization.map_figure import MapFigure
from spectromagpythontools.devices.pgm1se import PGM1SE
from spectromagpythontools.devices.kinesis_ppc102 import KinesisPPC102
assert 'matplotlib.backends.backend_tkagg' not in sys.modules
print('Headless imports passed')
"""
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), MPLBACKEND="Agg")
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_result_compatibility():
    from spectromagpythontools.data.map_result import MapResult, load_map_result
    from spectromagpythontools.routines import map_result

    assert map_result.MapResult is MapResult
    assert map_result.load_map_result is load_map_result


def test_source_transfer_includes_examples(tmp_path):
    import zipfile

    target = tmp_path / "source.zip"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/export_source_transfer.py"),
            "--project",
            str(ROOT),
            "--output",
            str(target),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(target) as archive:
        assert "examples/repeatable_map.py" in archive.namelist()
        assert "tests/fixtures/architecture_contracts.json" in archive.namelist()
        assert "src/spectromagpythontools/application/control.py" in archive.namelist()
