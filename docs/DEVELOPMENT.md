# Development

## Layout

- `devices/`: instrument wrappers, no raster logic.
- `routines/`: point acquisition, mapping, movement coordination and map results.
- `gui/`: operator/engineering interfaces and viewing.
- `src/.../scripts/`: packaged command-line entry points.
- `scripts/`: desktop launcher, source-transfer/speed tools and calibration shim.
- `examples/`: short editable experiment scripts; no hardware on import.
- `tests/`: offline regression tests. Keep them even when cleaning the repository.

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
