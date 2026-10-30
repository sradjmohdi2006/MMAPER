import os
import tempfile

import numpy as np
import rasterio
import wx

import main
from data_model import DEFAULT_STYLE, MapLayer, TerrainLayer, load_project, save_project

app = wx.App(False)
tmpdir = tempfile.mkdtemp(prefix="mmaproject_")

planet = MapLayer.new_empty("Planet", "EPSG:4326", {"name": "text"})
planet.add_property("length", "12742.0", "number")
planet.add_property("terrain_type", "rocky", "text")
planet.add_feature("Core", "Point", [0, 0], {"name": "Core", "temperature": 6000})

europe = MapLayer.new_empty("Europe", "EPSG:4326", {"name": "text", "population": "number"})
europe.add_feature("Berlin", "Point", [13.4, 52.5], {"name": "Berlin", "population": 3700000})
europe.add_feature("Paris", "Point", [2.35, 48.85], {"name": "Paris", "population": 2100000})
europe.parent = planet
planet.children.append(europe)

asia = MapLayer.new_empty("Asia", "EPSG:4326", {"name": "text"})
asia.add_feature("Tokyo", "Point", [139.7, 35.7], {"name": "Tokyo"})
asia.parent = planet
planet.children.append(asia)

moon = MapLayer.new_empty("Moon", "EPSG:4326", {"name": "text"})
moon.parent = europe
europe.children.append(moon)
moon.add_property("phase", "waxing", "text")

tif_path = os.path.join(tmpdir, "tiny.tif")
with rasterio.open(
    tif_path, "w", driver="GTiff", height=10, width=10, count=1,
    dtype="float64", crs="EPSG:4326", transform=rasterio.Affine(1, 0, 0, 0, -1, 0),
) as dst:
    dst.write(np.arange(100, dtype="float64").reshape(10, 10), 1)

terrain = TerrainLayer(tif_path)
terrain.add_property("unit", "m", "text")

layers = [planet, terrain]
proj_path = os.path.join(tmpdir, "world.agis")
save_project(proj_path, layers)
assert os.path.exists(proj_path)
print("save_project wrote file OK")

loaded = load_project(proj_path)
assert len(loaded) == 2
print("load_project top-level count OK")

p = loaded[0]
assert isinstance(p, MapLayer)
assert p.name == "Planet" and p.crs == "EPSG:4326"
assert p.schema == {"name": "text"}
assert p.properties["length"] == "12742.0" and p.prop_types["length"] == "number"
assert len(p.features) == 1 and p.features[0].properties["temperature"] == 6000
print("MapLayer fields roundtrip OK")

assert len(p.children) == 2
assert p.children[0].name == "Europe" and p.children[0].parent is p
assert p.children[1].name == "Asia"
assert p.children[0].children[0].name == "Moon"
assert p.children[0].children[0].parent is p.children[0]
assert p.children[0].children[0].properties["phase"] == "waxing"
assert len(p.children[0].features) == 2
assert p.children[0].schema["population"] == "number"
assert p.children[0].features[0].properties["population"] == 3700000
print("nested children + features roundtrip OK")

t = loaded[1]
assert isinstance(t, TerrainLayer)
assert t.name == "tiny.tif"
assert t.crs == "EPSG:4326"
assert t.properties["unit"] == "m"
assert t.bounds is not None
assert abs(t.bounds.left - 0.0) < 1e-9 and abs(t.bounds.top - 0.0) < 1e-9
assert t.bounds.right == 10.0 and t.bounds.bottom == -10.0
assert t.resolution == (1.0, 1.0)
assert t.stats.get("Minimum Elevation") == 0.0
assert t.stats.get("Maximum Elevation") == 99.0
assert t.get_geometry_summary().startswith("Bounds")
print("TerrainLayer roundtrip (from file) OK")

missing = TerrainLayer.from_dict({
    "kind": "TerrainLayer",
    "name": "lost",
    "filepath": os.path.join(tmpdir, "does_not_exist.tif"),
    "crs": "EPSG:4326",
    "bounds": [1.0, 2.0, 3.0, 4.0],
    "resolution": [1.5, 2.5],
    "stats": {"Minimum Elevation": -5.0},
    "properties": {"unit": "m"},
    "prop_types": {"unit": "text"},
    "children": [],
})
assert missing.bounds.left == 1.0 and missing.bounds.bottom == 2.0
assert missing.resolution == (1.5, 2.5)
assert missing.stats["Minimum Elevation"] == -5.0
assert missing.get_geometry_summary().startswith("Bounds")
print("TerrainLayer roundtrip (missing file, stored metadata) OK")

proj_path2 = os.path.join(tmpdir, "nest.agis")
save_project(proj_path2, [planet])
reloaded = load_project(proj_path2)
assert len(reloaded) == 1
r = reloaded[0]
assert len(r.all_members()) == 3
assert r.children[0].children[0].name == "Moon"
print("nested project file roundtrip OK")

planet.visible = False
europe.visible = False
proj_path3 = os.path.join(tmpdir, "vis.agis")
save_project(proj_path3, [planet])
v = load_project(proj_path3)[0]
assert v.visible is False
assert v.children[0].visible is False
assert v.children[1].visible is True
assert v.children[0].is_effectively_visible() is False
planet.visible = True
europe.visible = True
print("layer visibility persistence OK")

planet.set_style({"color": "#AA0000", "marker": "triangle", "line_width": 2.5,
                  "fill_alpha": 0.6, "marker_size": 8.0})
proj_path4 = os.path.join(tmpdir, "style.agis")
save_project(proj_path4, [planet])
sv = load_project(proj_path4)[0]
st = sv.style_with_defaults()
assert st["color"] == "#AA0000" and st["marker"] == "triangle"
assert st["line_width"] == 2.5 and st["fill_alpha"] == 0.6 and st["marker_size"] == 8.0
assert sv.children[0].style_with_defaults()["color"] == DEFAULT_STYLE["color"]
print("layer style persistence OK")

frame = main.MainFrame()
frame.layers = [planet, terrain]
frame._rebuild_tree()
assert frame.tree.GetChildrenCount(frame.root_node, recursively=False) == 2
node = frame._find_item_node(frame.root_node, planet)
assert node.IsOk()
assert frame.tree.GetChildrenCount(node, recursively=False) == 3  # 1 feature + 2 sub-layers
europe_node = frame._find_item_node(frame.root_node, planet.children[0])
assert europe_node.IsOk()
assert frame.tree.GetChildrenCount(europe_node, recursively=False) == 3  # 2 features + Moon
print("frame tree rebuild from loaded layers OK")

frame.layers = []
frame._rebuild_tree()
assert frame.tree.GetChildrenCount(frame.root_node, recursively=False) == 0
print("on_new_project clear path OK")

frame.Destroy()

bad = os.path.join(tmpdir, "bad.agis")
with open(bad, "w", encoding="utf-8") as f:
    f.write('{"type": "SomethingElse"}')
try:
    load_project(bad)
    raise AssertionError("expected load_project to reject non-project file")
except ValueError:
    pass
print("non-project file rejected OK")

print("ALL PROJECT SAVE/LOAD TESTS PASSED")
