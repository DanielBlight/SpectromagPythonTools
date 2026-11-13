# Development guide

## Keep the design small

- devices/: one understandable wrapper per instrument.
- routines/: measurement and automation functions using device objects.
- scripts/: argument handling, configuration, device setup and cleanup.
- gui/: user interfaces calling the same routines as scripts.
- tests/: offline tests using simple fake devices.
- config/: example settings and local machine configuration.
- data/: generated measurement output, not committed.
- logs/: generated logs, not committed.

Do not add service containers, dispatchers, plugin systems, generic device
frameworks or extra architectural layers without a concrete requirement.

## Dependency direction

Scripts and GUIs call routines.
Routines call device wrappers.
Device wrappers must not depend on routines, scripts or GUIs.

Keep package __init__.py files lightweight.
Importing a module must not connect to hardware, move equipment or open a GUI.
Load optional vendor SDKs when their device is explicitly opened, not during
ordinary package imports.

## Device wrappers

Use explicit connect and close operations.
Use finite communication timeouts.
Document units and validate commands against documented device limits.
Do not silently retry movement or other non-idempotent commands.
Make ownership clear: the code opening a device is responsible for closing it.

## Routines

Pass device objects and settings explicitly.
Start with ordinary functions rather than a framework.
Long routines should support cooperative cancellation and progress reporting.
Check cancellation between bounded operations.
Record settings, units and timestamps alongside measurement results.
Avoid overwriting existing measurement output.

## Scripts and GUIs

Keep measurement logic in routines so both interfaces share it.
Scripts must use an if __name__ == "__main__" guard.
Close devices in finally blocks or context managers.
GUIs must not run blocking acquisition on the GUI thread.
Choose a GUI toolkit when the first actual interface is implemented.

## Tests

Default tests must be offline and must not require vendor SDKs or GUI packages.
Use small fake devices to test routines.
Any future hardware tests must require explicit opt-in.

## Commands

uv run python -m spectromagpythontools.scripts.check_setup
uv run ruff check --fix .
uv run python -m pytest