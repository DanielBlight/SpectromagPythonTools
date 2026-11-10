"""Check package imports without connecting to instruments."""

import importlib


def main() -> None:
    for name in ("devices", "routines", "scripts", "gui"):
        importlib.import_module(f"spectromagpythontools.{name}")
    print("SpectromagPythonTools imports OK. No hardware was accessed.")


if __name__ == "__main__":
    main()