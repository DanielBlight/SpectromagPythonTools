# Development

## Layout

See [Architecture](ARCHITECTURE.md) for module ownership and dependency rules.

- `devices/`: wrappers, native transport and device-owned motion state.
- `routines/`: acquisition and experiment coordination.
- `application/`: GUI-independent sessions, worker and operator profiles.
- `data/`: offline CSV/results/export.
- `visualization/`: headless figures; `gui/`: Tk views and adapters.
- `diagnostics/`: timing and motion profiling.
- `cli/`: canonical command entry points; packaged `scripts/` retains old imports.
- Root `scripts/`: launch, benchmark, source-transfer and calibration tools.
- `examples/`: explicitly invoked experiments; import and --help access no hardware.
- `tests/`: regression and architecture contracts.

## Validate

With the development environment already installed:

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m ruff check --fix src tests scripts examples
python -m pytest -q
```

Or on a networked development PC using the existing uv project:

```powershell
uv run ruff check --fix src tests scripts examples
uv run pytest -q
```

pandas-specific result tests skip if pandas is absent; install pandas in the test
environment to exercise them. NumPy result tests similarly require NumPy.
Offline tests do not validate Windows DLL loading or actual hardware timing.

## Cleanup boundary

Historical `docs/archive/`, saved validation text and the redundant structure
write-up are removed. The calibration script in `scripts/` delegates to the
packaged implementation instead of duplicating it. All runtime drivers, GUI
modules, tests, source-transfer tools, launchers, lockfile and calibration logic
are retained. Engineering GUI remains available for diagnosis.

The updater checks reviewed file contents before modifying/removing them,
backs up affected originals outside the repository, and leaves unknown files,
local configuration, data, wheelhouse, environments and Git history untouched.
It does not recursively purge caches, downloads or unreviewed lab files.
