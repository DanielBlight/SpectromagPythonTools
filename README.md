# SpectromagPythonTools

Small instrument wrappers, automation routines and desktop controls for the
Spectromag laboratory setup. This is independent of Labtools.

## Desktop controls

    uv sync
    uv run python -m spectromagpythontools.scripts.commissioning_gui --tab mirror

Select the Photocurrent map tab in the same window, or launch with --tab map.
Startup does not connect to hardware. See docs/COMMISSIONING.md for installation,
configuration, controls, validation limits and the first hardware-test procedure.

Keep machine-specific addresses in config/instruments.local.toml (ignored by Git).
A shareable placeholder template is config/instruments.example.toml.

## Validation

    uv run ruff check --fix .
    uv run python -m pytest -q

Offline tests use synthetic fixtures, not real measurements. Actual mirror
motion requires verified count-to-angle conversion. Kinesis/VISA drivers are
installed separately. Device wrappers contain no mapping or GUI logic.
