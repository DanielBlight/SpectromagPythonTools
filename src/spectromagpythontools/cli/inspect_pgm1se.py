"""Read-only SDK inspection. Does NOT enable, centre, zero or move the mount."""

import argparse
import json
from dataclasses import asdict

from spectromagpythontools.devices.pgm1se import PGM1SE, KinesisPPC102


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--serial", required=True, help="Actual PPC102 serial (95xxxxxx)"
    )
    parser.add_argument(
        "--kinesis-directory", default=r"C:\Program Files\Thorlabs\Kinesis"
    )
    args = parser.parse_args()
    backend = KinesisPPC102(args.kinesis_directory)
    with PGM1SE(args.serial, backend=backend) as mount:
        print(
            json.dumps(
                {
                    "serial": args.serial,
                    "readback_basis": "SDK cached values; no hardware timestamp",
                    "axes": [asdict(state) for state in mount.raw_state()],
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
