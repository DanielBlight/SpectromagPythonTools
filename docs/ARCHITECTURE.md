# Architecture

## Assessment

The src layout and separation between device wrappers, acquisition routines and
operator/engineering views were sensible. The dependency direction was not:
the mirror driver imported routines for its own cache, scan settings and timing;
GUI modules owned reusable hardware sessions, operator profiles, offline CSV
loading and headless figures. The 893-line mirror module also mixed ctypes DLL
transport, value types and mount behaviour. This made non-GUI reuse harder.

## Canonical ownership

- `devices/keithley2600.py`: Keithley connection, configuration and acquisition.
- `devices/kinesis_ppc102.py`: native PPC102 DLL binding and controller transport.
- `devices/gimbal_types.py`: calibration, positions, raw state and errors.
- `devices/pgm1se.py`: mount preparation and movement, with public type re-exports.
- `devices/scan_position_cache.py`, `devices/scan_settings.py`: device-owned
  scan-scoped state and controller settings validation.
- `routines/`: experiment coordination, point acquisition, raster acquisition and
  the acquire-and-return-result convenience function. No GUI imports.
- `application/`: hardware Session/HardwareWorker, OperatorSession and profiles.
  These can be used without Tk. One worker still owns the instruments.
- `data/`: offline CSV loading, MapResult and exclusive PNG writing. No devices.
- `visualization/map_figure.py`: reusable Matplotlib figure model. No Tk/devices.
- `gui/`: Tk operator/engineering views, preferences, lock and plot adapter.
- `diagnostics/`: motion profiling and summary enrichment, without device imports.
- `cli/`: canonical packaged command entry points.
- `scripts/`: repository launch, calibration shim, benchmarking and source transfer.
- `examples/`: explicit experiment entry points. Import and --help are inert.

`configuration.py` remains a small composition module for TOML parsing,
validation and injectable device construction. Splitting its 136 lines into a
configuration framework would add complexity without a useful boundary.
The operator and commissioning views remain separate: they expose deliberately
different workflows. No plugin system, generic device registry or new framework
has been introduced.

## Compatibility and behaviour

Old relocated module paths are small aliases to the canonical module, not copies.
This preserves shared state and monkeypatch behaviour. Existing `python -m`
commands and `spectromag-gui` continue to work. `gui.map_plot` still exports
MapFigure/edges, and `routines.map_result` still exports MapResult/load_map_result.
New code should use the canonical owners above. No deprecated import warnings
are emitted into the operator interface.

Acquisition averaging, cancellation, output-off cleanup, motion algorithms,
calibration validation, environment settings, CSV format and GUI workflow are
not redesigned. AST contract tests check that the extracted mirror classes are
identical to the reviewed originals except for their local import paths.

## Snapshot limitations

The supplied text contains 66 file sections, but no instrument configuration or
examples. The baseline had 336 passing tests and five failures caused solely by
missing example files. Three runnable examples have been reconstructed to match
the documented workflows and existing tests. These are new examples, not claimed
recoveries of missing originals. The updater preserves any existing examples on
the lab PC. No serial number, calibration, DUT limit or profile is invented.

## Validation

Run `python -m ruff check --fix src tests scripts examples`, then
`python -m pytest -q`. Tests include layer boundaries, legacy module identity,
class AST contracts, headless imports, old/new CLI help and source transfer.
The original regression tests remain, with only an ambiguous local variable
renamed in one test for lint. Hardware and an interactive Windows GUI cannot be
validated in the offline Linux test environment. Use the existing launcher for
a short lab smoke check after applying the update.

The source-transfer utility now includes examples, preventing the same omission
when exporting future source bundles. Runtime data, local profiles, environments
and Git metadata remain excluded.
