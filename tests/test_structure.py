"""Offline smoke tests for the initial package structure."""

import importlib

import pytest


@pytest.mark.parametrize("name", ["devices", "routines", "scripts", "gui"])
def test_subpackage_imports(name: str) -> None:
    module = importlib.import_module(f"spectromagpythontools.{name}")
    assert module is not None


def test_setup_check(capsys: pytest.CaptureFixture[str]) -> None:
    from spectromagpythontools.scripts.check_setup import main

    main()

    assert "No hardware was accessed." in capsys.readouterr().out