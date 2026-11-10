"""Launch the mirror-control and photocurrent-map desktop tabs."""

import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=Path("config/instruments.local.toml")
    )
    parser.add_argument("--tab", choices=("mirror", "map"), default="mirror")
    args = parser.parse_args()
    try:
        from spectromagpythontools.gui.commissioning import launch
        from spectromagpythontools.gui.instance import single_instance

        with single_instance(args.config.resolve().parent.parent):
            launch(args.config, args.tab)
    except ImportError as exc:
        raise SystemExit(
            f"GUI dependency unavailable: {exc}. Test this interpreter with python -m tkinter. "
            "Tk must be included in the Python installation; pip cannot install it."
        ) from exc


if __name__ == "__main__":
    main()
