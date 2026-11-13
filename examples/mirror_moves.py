"""Explicitly prepare closed loop, then make two real absolute moves in mrad."""

import argparse
import os

from spectromagpythontools.configuration import build_mount, load_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/instruments.local.toml")
    config = load_config(parser.parse_args().config)
    previous = os.environ.get("SPECTROMAG_MIRROR_POLICY")
    os.environ["SPECTROMAG_MIRROR_POLICY"] = "verified"
    try:
        with build_mount(config["pgm1se"]) as mirror:
            mirror.prepare_closed_loop()
            print("Move 1:", mirror.move_and_wait(0.0, 0.0))
            # Other experiment work can go here. Moves block until settled.
            print("Move 2:", mirror.move_and_wait(0.1, 0.0))
        # Disconnecting does not command a return to zero.
    finally:
        if previous is None:
            os.environ.pop("SPECTROMAG_MIRROR_POLICY", None)
        else:
            os.environ["SPECTROMAG_MIRROR_POLICY"] = previous


if __name__ == "__main__":
    main()
