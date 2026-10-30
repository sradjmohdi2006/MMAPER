import json
import os
import tempfile

import numpy as np
import wx

import main
from data_model import TerrainLayer, _decimate_for_memory, load_project, save_project

app = wx.App(False)
frame = main.MainFrame()
tmpdir = tempfile.mkdtemp(prefix="mmaraster_")

layer = TerrainLayer.new_empty("Mars", "EPSG:4326", width=10, height=10)
assert layer.data.shape == (10, 10)
assert layer.bounds is not None and layer.resolution == (1.0, 1.0)
assert layer.transform is not None
assert layer.stats["Minimum Elevation"] == 0.0
assert layer.stats["Maximum Elevation"] == 0.0
assert layer.filepath is None
print("new_empty raster OK")

layer.fill(5)
assert (layer.data == 5).all()
assert layer.stats["Minimum Elevation"] == 5.0 and layer.stats["Maximum Elevation"] == 5.0
print("fill OK")

layer.fill_rect(2, 5, 3, 7, 9)
assert layer.data[3, 4] == 9
assert layer.data[1, 4] == 5
assert layer.data[2, 2] == 5
print("fill_rect OK")

layer.add_constant(10)
assert layer.data[3, 4] == 19 and layer.data[0, 0] == 15
print("add_constant OK")

layer.multiply_constant(2)
assert layer.data[3, 4] == 38 and layer.data[0, 0] == 30
print("multiply_constant OK")

g = TerrainLayer.new_empty("G", width=5, height=5)
g.add_gradient("North->South", 0, 100)
assert g.data[0, 0] < g.data[2, 0] < g.data[4, 0]
assert abs(g.data[4, 0] - 100.0) < 1e-9
assert g.stats["Maximum Elevation"] == 100.0
print("add_gradient OK")

g.invert()
assert abs(g.data[0, 0] - 100.0) < 1e-9
assert abs(g.data[4, 0] - 0.0) < 1e-9
assert g.stats["Minimum Elevation"] == 0.0
print("invert OK")

before_std = g.stats["Std Deviation"]
g.add_noise(20)
assert g.stats["Std Deviation"] != before_std
g.smooth(2)
assert g.stats["Std Deviation"] < before_std + 1e-9
print("add_noise + smooth OK")

value = layer.sample_elevation(4.5, 4.5)
assert isinstance(value, float)
value = g.sample_elevation(2.5, 2.5)
assert isinstance(value, float)
print("sample_elevation from in-memory raster OK")

tif_path = os.path.join(tmpdir, "mars.tif")
layer.save_to_geotiff(tif_path)
assert os.path.exists(tif_path)
reloaded = TerrainLayer(tif_path)
np.testing.assert_array_equal(reloaded.data, layer.data)
assert reloaded.resolution == layer.resolution
assert reloaded.crs == layer.crs
print("save_to_geotiff + reload OK")

proj_path = os.path.join(tmpdir, "raster.agis")
save_project(proj_path, [layer, g])
loaded = load_project(proj_path)
assert len(loaded) == 2
t0 = loaded[0]
np.testing.assert_array_equal(t0.data, layer.data)
assert t0.transform is not None
assert t0.bounds is not None
assert t0.filepath == tif_path
assert t0.stats == layer.stats
t1 = loaded[1]
np.testing.assert_array_equal(t1.data, g.data)
print("project roundtrip embeds raster data OK")

dlg = main.NewRasterDialog(frame)
dlg.name_ctrl.SetValue("Mars2")
dlg.width_ctrl.SetValue("50")
dlg.height_ctrl.SetValue("40")
dlg.bounds_ctrl.SetValue("0, 0, 500, 400")
dlg.value_ctrl.SetValue("7")
name, crs, width, height, bounds, value, bands = dlg.get_data()
assert name == "Mars2" and crs == "EPSG:4326"
assert width == 50 and height == 40
assert bounds == (0.0, 0.0, 500.0, 400.0)
assert value == 7.0
assert bands == 1
dlg.bands_ctrl.SetValue("3")
name, crs, width, height, bounds, value, bands = dlg.get_data()
assert bands == 3
dlg.Destroy()
print("NewRasterDialog get_data OK")

r = TerrainLayer.new_empty("T", width=10, height=10)
rdlg = main.RasterEditDialog(frame, r)
rdlg.op_ctrl.SetSelection(rdlg.OP_FILL)
rdlg.controls["fill_value"].SetValue("3")
rdlg.on_apply(None)
assert (r.data == 3).all()
assert r.stats["Minimum Elevation"] == 3.0
assert rdlg.stats_ctrl.GetItemCount() >= 4

rdlg.op_ctrl.SetSelection(rdlg.OP_RECT)
rdlg.controls["r0"].SetValue("1")
rdlg.controls["r1"].SetValue("4")
rdlg.controls["c0"].SetValue("2")
rdlg.controls["c1"].SetValue("6")
rdlg.controls["rect_value"].SetValue("9")
rdlg.on_apply(None)
assert r.data[2, 3] == 9 and r.data[0, 0] == 3

rdlg.op_ctrl.SetSelection(rdlg.OP_INVERT)
rdlg.on_apply(None)
assert r.stats["Minimum Elevation"] == 3.0

rdlg.controls["fill_value"].SetValue("abc")
try:
    rdlg._num("fill_value")
    raise AssertionError("expected ValueError for non-numeric param")
except ValueError:
    pass
rdlg.Destroy()
print("RasterEditDialog operations + param validation OK")

# --- compressed embedded band storage ---
with open(proj_path, "r", encoding="utf-8") as f:
    raw = json.load(f)
terrain_dict = next(d for d in raw["layers"] if d["kind"] == "TerrainLayer")
assert terrain_dict["bands"][0]["encoding"] == "zlib+base64"
assert terrain_dict["bands"][0]["shape"] == [10, 10]
assert len(terrain_dict["bands"][0]["data"]) < 2000  # tiny vs list-of-lists
print("compressed zlib+base64 band storage OK")

# --- legacy list-of-lists band format still loads ---
legacy = TerrainLayer.from_dict({
    "kind": "TerrainLayer",
    "name": "legacy",
    "filepath": None,
    "crs": "EPSG:4326",
    "bounds": [0.0, 0.0, 2.0, 2.0],
    "resolution": [1.0, 1.0],
    "bands": [[[1.0, 2.0], [3.0, 4.0]]],
    "stats": {},
})
assert legacy.data.shape == (2, 2)
assert legacy.data[1, 1] == 4.0
assert legacy.original_shape == (2, 2) and legacy.reduced is False
print("legacy list-of-lists band format OK")

# --- memory-cap decimation helper ---
big = np.arange(10000, dtype="float64").reshape(100, 100)
red, factor = _decimate_for_memory(big, 500)
assert red.shape[0] * red.shape[1] <= 500
assert red.shape != big.shape and factor > 1.0
small, f2 = _decimate_for_memory(big, 10 ** 9)
assert small.shape == big.shape and f2 == 1.0
print("memory-cap decimation helper OK")

# --- raster colormap style persistence ---
clr = TerrainLayer.new_empty("CM", "EPSG:4326", width=6, height=6)
clr.set_style({"colormap": "plasma"})
proj2 = os.path.join(tmpdir, "cm.agis")
save_project(proj2, [clr])
cl = load_project(proj2)[0]
assert cl.style_with_defaults()["colormap"] == "plasma"
assert cl.original_shape == (6, 6) and cl.reduced is False
print("raster colormap style persistence OK")

# --- RasterStyleDialog get_style ---
rsdlg = main.RasterStyleDialog(frame, layer)
rsdlg.cmap_ctrl.SetStringSelection("inferno")
style_out = rsdlg.get_style()
assert style_out["colormap"] == "inferno"
assert style_out["render_mode"] == "single"
assert style_out["rgb_bands"] == [1, 1, 1]
rsdlg._draw_preview()
rsdlg.Destroy()
print("RasterStyleDialog get_style OK")

# --- multi-band support ---
mb = TerrainLayer.new_empty("MB", "EPSG:4326", width=10, height=10, bands=3)
assert mb.band_count == 3
assert len(mb.bands) == 3
assert mb.active_band == 1
assert mb.band_names == ["Band 1", "Band 2", "Band 3"]
assert mb.data is mb.bands[0]
mb.fill(1)
assert (mb.bands[0] == 1).all()
assert mb.set_active_band(2) is True
assert mb.data is mb.bands[1]
mb.fill(2)
assert mb.set_active_band(3) is True
mb.fill(3)
assert (mb.bands[0] == 1).all() and (mb.bands[1] == 2).all() and (mb.bands[2] == 3).all()
assert mb.set_active_band(99) is False
assert mb.set_active_band(0) is False
print("multi-band new_empty + per-band edit OK")

assert mb.add_band() is True
assert mb.band_count == 4
assert mb.band_names[-1] == "Band 4"
assert mb.add_band(name="Red") is True
assert mb.band_names[-1] == "Red"
assert mb.band_count == 5
assert mb.duplicate_band(2) is True
assert mb.band_count == 6
assert mb.band_names[5] == "Band 2 Copy"
np.testing.assert_array_equal(mb.bands[5], mb.bands[1])
assert mb.rename_band(3, "Green") is True
assert mb.band_names[2] == "Green"
assert mb.remove_band(6) is True
assert mb.band_count == 5
assert mb.band_names == ["Band 1", "Band 2", "Green", "Band 4", "Red"]
assert mb.remove_band(1) is True
assert mb.band_count == 4
assert mb.remove_band(1) is True
assert mb.remove_band(1) is True
assert mb.remove_band(1) is True
assert mb.remove_band(1) is False
assert mb.band_count == 1
print("band add/duplicate/rename/remove OK")

rgb = mb.rgb_array(r_band=1, g_band=1, b_band=1)
assert rgb.shape == (6, 6, 3) or rgb.shape == (10, 10, 3)
assert rgb.ndim == 3 and rgb.shape[2] == 3
assert rgb.dtype == np.float32
assert float(np.nanmin(rgb)) >= 0.0 and float(np.nanmax(rgb)) <= 1.0
assert np.isfinite(rgb).all()
print("rgb_array shape + normalisation OK")

# --- multi-band project roundtrip (band_names + active_band persist) ---
mb2 = TerrainLayer.new_empty("MB2", "EPSG:4326", width=6, height=6, bands=2)
mb2.set_active_band(2)
mb2.rename_band(1, "Elevation")
mb2.rename_band(2, "Slope")
mb2.set_style({"colormap": "plasma", "render_mode": "rgb", "rgb_bands": [1, 2, 2]})
proj3 = os.path.join(tmpdir, "mb.agis")
save_project(proj3, [mb2])
loaded_mb = load_project(proj3)[0]
assert loaded_mb.band_count == 2
assert loaded_mb.band_names == ["Elevation", "Slope"]
assert loaded_mb.active_band == 2
assert loaded_mb.style_with_defaults()["render_mode"] == "rgb"
assert loaded_mb.style_with_defaults()["rgb_bands"] == [1, 2, 2]
np.testing.assert_array_equal(loaded_mb.bands[0], mb2.bands[0])
np.testing.assert_array_equal(loaded_mb.bands[1], mb2.bands[1])
print("multi-band project roundtrip + band_names OK")

# --- multi-band GeoTIFF save/reload ---
mb_tif = os.path.join(tmpdir, "mb.tif")
mb2.save_to_geotiff(mb_tif)
mb_reloaded = TerrainLayer(mb_tif)
assert mb_reloaded.band_count == 2
assert mb_reloaded.band_names == ["Band 1", "Band 2"]
for i in range(2):
    np.testing.assert_array_equal(mb_reloaded.bands[i], mb2.bands[i])
print("multi-band GeoTIFF save/reload OK")

# --- RasterEditDialog band selection with multiple bands ---
med = main.RasterEditDialog(frame, mb2)
assert med.band_ctrl.GetCount() == 2
med.band_ctrl.SetSelection(1)
med.on_band_change(None)
assert mb2.active_band == 2
med.band_ctrl.SetSelection(0)
med.on_band_change(None)
assert mb2.active_band == 1
assert med.stats_ctrl.GetItemCount() >= 4
med.Destroy()
print("RasterEditDialog multi-band band selection OK")

# --- RasterPreviewDialog RGB render ---
pdlg = main.RasterPreviewDialog(frame, mb2)
assert pdlg.mode_ctrl.GetSelection() == 0
pdlg.mode_ctrl.SetSelection(1)
pdlg.r_ctrl.SetSelection(0)
pdlg.g_ctrl.SetSelection(1)
pdlg.b_ctrl.SetSelection(1)
pdlg.render()
assert pdlg.fig.axes[0].get_title() == "RGB composite"
pdlg.mode_ctrl.SetSelection(0)
pdlg.render()
assert pdlg.fig.axes[0].get_title() != "RGB composite"
pdlg.Destroy()
print("RasterPreviewDialog RGB render OK")

# --- RasterStyleDialog RGB style ---
sdlg = main.RasterStyleDialog(frame, mb2)
sdlg.mode_ctrl.SetSelection(1)
sdlg.r_ctrl.SetSelection(0)
sdlg.g_ctrl.SetSelection(1)
sdlg.b_ctrl.SetSelection(1)
style_out = sdlg.get_style()
assert style_out["render_mode"] == "rgb"
assert style_out["rgb_bands"] == [1, 2, 2]
sdlg._draw_preview()
sdlg.Destroy()
print("RasterStyleDialog RGB style OK")

# --- BandAddDialog + BandManagerDialog ---
mbg = TerrainLayer.new_empty("MGB", width=6, height=6)
bmd = main.BandManagerDialog(frame, mbg)
assert bmd.list_ctrl.GetItemCount() == 1
add_dlg = main.BandAddDialog(frame, 1)
add_dlg.count_ctrl.SetValue("2")
assert add_dlg.get_count() == 2
add_dlg.Destroy()
bmd.list_ctrl.Select(0)
bmd.on_set_active(None)
assert mbg.active_band == 1
assert bmd.changed is True
bmd.on_duplicate(None)
assert mbg.band_count == 2
assert mbg.band_names == ["Band 1", "Band 1 Copy"]
bmd.list_ctrl.Select(1)
bmd.on_set_active(None)
assert mbg.active_band == 2
assert bmd.list_ctrl.GetItemCount() == 2
bmd.Destroy()
print("BandAddDialog + BandManagerDialog OK")

# --- populate_terrain shows active band name ---
frame.current_terrain = mb2
mb2.set_active_band(2)
frame.populate_terrain(mb2)
row_labels = [frame.list_ctrl.GetItemText(i, 0) for i in range(frame.list_ctrl.GetItemCount())]
row_values = [frame.list_ctrl.GetItemText(i, 1) for i in range(frame.list_ctrl.GetItemCount())]
assert "Active Band" in row_labels
assert "2 of 2 (Slope)" in row_values
assert any("RGB composite" in v for v in row_values)
print("populate_terrain active band name OK")

frame.Destroy()
print("ALL RASTER TESTS PASSED")
