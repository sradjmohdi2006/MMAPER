import wx
import main
from data_model import Layer, MapLayer, TerrainLayer

app = wx.App(False)
frame = main.MainFrame()

def select_and_fire(item_id):
    frame.tree.SelectItem(item_id)
    evt = wx.TreeEvent(wx.wxEVT_TREE_SEL_CHANGED, frame.tree, item_id)
    frame.on_tree_select(evt)

planet = MapLayer.new_empty("Planet", "EPSG:4326", {"name": "text"})
frame.add_layer(planet)

continents = ["Africa", "Asia", "Europe", "North America", "South America", "Oceania", "Antarctica"]
layers = []
for name in continents:
    lyr = MapLayer.new_empty(name, "EPSG:4326", {"name": "text"})
    lyr.add_feature(name, "Point", [1, 2], {"name": name})
    lyr.parent = planet
    planet.children.append(lyr)
    layers.append(lyr)
frame._rebuild_tree()

assert len(frame.all_layers()) == 8, len(frame.all_layers())
assert len(planet.all_members()) == 7
node = frame._find_item_node(frame.root_node, planet)
assert node.IsOk()
assert frame.tree.GetChildrenCount(node, recursively=False) == 7
assert planet.children[0].parent is planet
print("7 continent sub-layers inside Planet layer OK")

assert planet.contains(planet) is True
assert planet.contains(layers[0]) is True
assert layers[0].contains(planet) is False
sub = MapLayer.new_empty("West Asia", "EPSG:4326", {})
sub.parent = layers[1]
layers[1].children.append(sub)
assert layers[1].contains(sub) is True
assert planet.contains(sub) is True
frame._detach(sub)
assert sub not in layers[1].children
assert sub.parent is None
print("contains / cycle protection / detach OK")

select_and_fire(node)
assert frame.current_layer is planet
print("planet layer summary OK")

frame._detach(layers[0])
assert layers[0] not in planet.children and layers[0].parent is None
frame.layers.append(layers[0])
frame._rebuild_tree()
assert len(frame.all_layers()) == 8
print("detach from parent to top level OK")

# --- layer properties (layer itself carries settable attributes) ---
planet.add_property("length", "12742.0", "number")
planet.add_property("terrain", "rocky", "text")
planet.add_property("has_oceans", True, "boolean")
assert planet.properties["length"] == "12742.0"
planet.rename_property("terrain", "terrain_type", "text")
assert "terrain" not in planet.prop_types and planet.prop_types["terrain_type"] == "text"
assert planet.properties["terrain_type"] == "rocky"
planet.remove_property("has_oceans")
assert "has_oceans" not in planet.prop_types and "has_oceans" not in planet.properties
print("layer add/rename/remove property OK")

pldg = main.FieldListDialog(frame, None, None, group=planet)
assert pldg.list_ctrl.GetItemCount() == 2
pldg._select_field("length")
pldg._set_value("length", "6371.0")
assert planet.properties["length"] == 6371.0 and type(planet.properties["length"]) is float
pldg._set_value("terrain_type", "rocky & icy")
assert planet.properties["terrain_type"] == "rocky & icy"
pldg.refresh_list()
pldg.Destroy()
print("FieldListDialog layer-property mode + type coercion OK")

frame.current_layer = planet
frame.populate_layer_summary(planet)
rows = [frame.list_ctrl.GetItemText(i, 0) for i in range(frame.list_ctrl.GetItemCount())]
assert "length" in rows and "terrain_type" in rows
assert frame.list_ctrl.GetItemText(rows.index("length"), 1) == "6371.0"
print("layer summary shows layer properties OK")

frame._set_layer_property(planet, "length", "100")
assert planet.properties["length"] == 100.0
frame._set_layer_property(planet, "is_giant", "true")
assert planet.properties["is_giant"] == "true" and planet.prop_types["is_giant"] == "text"
print("_set_layer_property coercion OK")

# --- position logic ---
frame.tree.SelectItem(node)
position = (planet, True)
new_layer = MapLayer.new_empty("Moon", "EPSG:4326", {})
frame.add_layer(new_layer, position)
assert new_layer in planet.children and new_layer.parent is planet
assert len(frame.all_layers()) == 9
position = (planet, False)
new_layer2 = MapLayer.new_empty("Venus", "EPSG:4326", {})
frame.add_layer(new_layer2, position)
assert new_layer2 in frame.layers and new_layer2.parent is None
print("add_layer child / sibling placement OK")

frame._clear_selection()
assert frame.current_layer is None and frame.current_feature is None
print("clear selection OK")

# --- drag-and-drop re-nesting ---
moon = next((l for l in frame.all_layers() if l.name == "Moon"), None)
venus = next((l for l in frame.all_layers() if l.name == "Venus"), None)
planet_live = next((l for l in frame.layers if l.name == "Planet"), None)
assert moon and venus and planet_live
moon_node = frame._find_item_node(frame.root_node, moon)
venus_node = frame._find_item_node(frame.root_node, venus)
assert moon_node.IsOk() and venus_node.IsOk()
frame.on_tree_begin_drag(wx.TreeEvent(wx.wxEVT_TREE_BEGIN_DRAG, frame.tree, moon_node))
frame.on_tree_end_drag(wx.TreeEvent(wx.wxEVT_TREE_END_DRAG, frame.tree, venus_node))
assert moon in venus.children and moon.parent is venus
assert moon not in planet_live.children
print("drag-drop re-nest under another layer OK")

frame.on_undo(None)
moon = next((l for l in frame.all_layers() if l.name == "Moon"), None)
planet_live = next((l for l in frame.layers if l.name == "Planet"), None)
assert moon and moon in planet_live.children and moon.parent is planet_live
print("drag-drop re-nest undo OK")

planet_node = frame._find_item_node(frame.root_node, planet_live)
moon_node = frame._find_item_node(frame.root_node, moon)
real_msgbox = wx.MessageBox
wx.MessageBox = lambda *a, **k: wx.YES
frame.on_tree_begin_drag(wx.TreeEvent(wx.wxEVT_TREE_BEGIN_DRAG, frame.tree, planet_node))
frame.on_tree_end_drag(wx.TreeEvent(wx.wxEVT_TREE_END_DRAG, frame.tree, moon_node))
wx.MessageBox = real_msgbox
assert planet_live in frame.layers and moon in planet_live.children
print("drag-drop cycle rejection OK")

moon_node = frame._find_item_node(frame.root_node, moon)
frame.on_tree_begin_drag(wx.TreeEvent(wx.wxEVT_TREE_BEGIN_DRAG, frame.tree, moon_node))
frame.on_tree_end_drag(wx.TreeEvent(wx.wxEVT_TREE_END_DRAG, frame.tree, frame.root_node))
assert moon.parent is None and moon in frame.layers
print("drag-drop to top level OK")

# --- layer visibility toggles ---
asia = next((l for l in frame.all_layers() if l.name == "Asia"), None)
frame.current_layer = planet_live
assert planet_live.visible is True
assert planet_live.is_effectively_visible() is True
assert asia.is_effectively_visible() is True
frame.on_toggle_visibility(None)
assert planet_live.visible is False
assert planet_live.is_effectively_visible() is False
assert asia.is_effectively_visible() is False
node = frame._find_item_node(frame.root_node, planet_live)
assert node.IsOk()
assert frame.tree.GetItemText(node) == "Planet (hidden)"
print("visibility toggle + tree label OK")

frame.on_undo(None)
planet_live = next((l for l in frame.all_layers() if l.name == "Planet"), None)
asia = next((l for l in frame.all_layers() if l.name == "Asia"), None)
assert planet_live.visible is True
frame.current_layer = planet_live
frame.on_toggle_visibility(None)
assert asia.is_effectively_visible() is False
frame.on_toggle_visibility(None)
assert planet_live.visible is True
assert asia.is_effectively_visible() is True
print("visibility toggle undo + effective visibility OK")

# --- layer draw order within a parent (z-order) ---
asia = next((l for l in frame.all_layers() if l.name == "Asia"), None)
prev_index = planet_live.children.index(asia)
while planet_live.children.index(asia) > 0:
    assert frame._move_layer(asia, -1)
assert planet_live.children.index(asia) == 0
assert frame._move_layer(asia, -1) is False
assert frame._move_layer(asia, 1)
while planet_live.children.index(asia) < prev_index:
    frame._move_layer(asia, 1)
while planet_live.children.index(asia) > prev_index:
    frame._move_layer(asia, -1)
assert planet_live.children.index(asia) == prev_index
assert [l.name for l in frame.map_canvas._legend_layers] == [l.name for l in frame.all_layers()]
print("nested layer draw order (z-order) OK")

frame.Destroy()
print("ALL NESTING TESTS PASSED")
