"""Measure using [photocurrent_map] settings. No hardware access on import."""

import argparse

from spectromagpythontools.configuration import build_smu, load_config, measurement
from spectromagpythontools.routines.photocurrent_map import read_point


def measure(config_path):
    config = load_config(config_path)
    values = config["photocurrent_map"]
    max_v, max_i, settings = measurement(values)
    with build_smu(
        config["keithley"], max_abs_bias_v=max_v, max_current_a=max_i
    ) as smu:
        smu.configure(settings)
        with smu.output_enabled():
            return read_point(
                smu,
                samples=values.get("samples_per_pixel", 3),
                settle_s=values.get("pixel_settle_s", 0.1),
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/instruments.local.toml")
    point = measure(parser.parse_args().config)
    print(f"Mean current: {point.mean_current_a:.9g} A")
    print(f"Sample standard deviation: {point.sample_std_current_a} A")
    print(f"Mean voltage: {point.mean_voltage_v:.9g} V")
    for reading in point.readings:
        print(reading)


if __name__ == "__main__":
    main()
