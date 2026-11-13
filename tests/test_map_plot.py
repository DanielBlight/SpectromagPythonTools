"""Headless scientific plotting regressions."""

import pytest
from matplotlib.backends.backend_agg import FigureCanvasAgg

from spectromagpythontools.gui.map_plot import MapFigure, edges


def test_reuses_artists_and_masks_missing():
    plot = MapFigure()
    image, bar = plot.image, plot.colorbar
    plot.update((0, 1), (0, 1), {(0, 1): 2e-9})
    assert plot.image is image and plot.colorbar is bar
    assert plot.image.get_array().mask[0, 0]
    assert plot.image.get_array()[0, 1] == 2e-9
    assert tuple(plot.image.get_extent()) == (-0.5, 1.5, -0.5, 1.5)
    assert plot.image.origin == "lower"
    assert plot.image.get_cmap().name == "inferno"
    FigureCanvasAgg(plot.figure).draw()


@pytest.mark.parametrize("cmap", ["inferno", "magma"])
def test_auto_uses_data_range(cmap):
    plot = MapFigure()
    plot.update((0, 1), (0,), {(0, 0): 2e-9, (0, 1): 3e-9}, cmap=cmap)
    assert plot.image.get_clim() == (2e-9, 3e-9)
    assert plot.image.get_cmap().name == cmap


def test_fixed_limits_and_diverging():
    plot = MapFigure()
    plot.update((0,), (0,), {(0, 0): 1e-9}, mode="Fixed", limits=(-2e-9, 2e-9))
    assert plot.image.get_clim() == (-2e-9, 2e-9)
    plot.update((0,), (0,), {(0, 0): -1e-9}, mode="Zero-centred")
    assert plot.image.norm(0) == 0.5
    assert plot.image.get_cmap().name == "RdBu_r"


@pytest.mark.parametrize(
    "limits", [(1, 1), (2, 1), (float("nan"), 1), (0, float("inf"))]
)
def test_invalid_limits(limits):
    with pytest.raises(ValueError):
        MapFigure().update((0,), (0,), {}, mode="Fixed", limits=limits)


def test_empty_zero_and_descending_axes():
    plot = MapFigure()
    plot.update((1, 0), (2, 1), {})
    assert edges((1, 0)) == (1.5, -0.5)
    plot.update((0,), (0,), {(0, 0): 0})
    assert plot.image.norm.vmin < 0 < plot.image.norm.vmax
    FigureCanvasAgg(plot.figure).draw()
