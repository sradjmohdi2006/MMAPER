import os
import tempfile
import json
import wx
import main
from data_model import (DEFAULT_STYLE, MARKER_CHOICES, MapLayer, Feature, FIELD_TEMPLATES,
                        TerrainLayer)

app = wx.App(False)
frame = main.MainFrame()

# --- AddFeatureDialog: X/Y sub-field point collection ---
dlg = main.AddFeatureDialog(frame)
dlg.name_ctrl.SetValue("Test Point")
dlg.x_ctrl.SetValue("10.5")
dlg.y_ctrl.SetValue("-3.25")
dlg.on_add_point(None)
assert dlg.points == [[10.5, -3.25]]
assert dlg.points_ctrl.GetItemCount() == 1
assert dlg.get_data()[2] == [10.5, -3.25]
dlg.type_ctrl.SetSelection(0)
name, gtype, coords, props = dlg.get_data()
assert (name, gtype, coords) == ("Test Point", "Point", [10.5, -3.25])
print("Add Feature: single Point via X/Y OK")

dlg.on_add_point(None)
dlg.x_ctrl.SetValue("20.0"); dlg.y_ctrl.SetValue("40.0"); dlg.on_add_point(None)
dlg.type_ctrl.SetSelection(1)
name, gtype, coords, props = dlg.get_data()
assert gtype == "LineString" and coords == [[10.5, -3.25], [20.0, 40.0]]
print("Add Feature: LineString assembled OK")

dlg.points = [[0, 0], [1, 0], [1, 1]]
dlg.type_ctrl.SetSelection(2)
name, gtype, coords, props = dlg.get_data()
assert gtype == "Polygon"
assert coords[0][0] == [0, 0] and coords[0][-1] == [0, 0]
print("Add Feature: Polygon ring auto-closed OK")

parsed = dlg._parse_points_text("1,2\n3.5,4\n\n5,6")
assert parsed == [[1.0, 2.0], [3.5, 4.0], [5.0, 6.0]]
flattened = dlg._flatten_points([[1, 2], [[3, 4], [5, 6]]])
assert flattened == [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]
print("Paste points parser OK")
dlg.Destroy()

# --- data_model: templates, fields, rename, split coords, geo fields ---
layer = MapLayer.new_empty("Census", "EPSG:4326", {})
assert FIELD_TEMPLATES.get("Demographics"), "Demographics template missing"
for key, ftype in FIELD_TEMPLATES["Demographics"].items():
    layer.add_field(key, ftype)
assert "population" in layer.schema
print("Field template applied OK")

layer.add_feature("City A", "Point", [30, 50], {"population": 100, "name": "City A"})
layer.add_feature("City B", "Point", [31, 51], {"population": 200, "name": "City B"})
layer.rename_field("population", "pop")
assert "population" not in layer.schema and layer.schema["pop"] == "number"
assert all("pop" in f.properties and "population" not in f.properties for f in layer.features)
print("rename_field OK")

layer.remove_field("pop")
assert "pop" not in layer.schema and all("pop" not in f.properties for f in layer.features)
layer.add_field("pop", "integer", 0)
print("add/remove field OK")

ok = layer.split_geometry_coordinates(layer.features[0])
assert ok and layer.features[0].properties["x"] == 30.0 and layer.features[0].properties["y"] == 50.0
assert layer.schema["x"] == "number"
layer.add_centroid_field("centroid")
assert "centroid" in layer.features[0].properties
layer.add_geometry_fields()
assert "area" in layer.schema or "length" in layer.schema
print("coordinate split / centroid / geometry fields OK")

layer.buffer_feature(layer.features[0], 0.1)
assert layer.features[0].geometry["type"] == "Polygon"
layer.simplify_feature(layer.features[0], 0.001)
print("buffer / simplify OK")

with tempfile.TemporaryDirectory() as td:
    p = os.path.join(td, "out.geojson")
    layer.save_to_geojson(p)
    loaded = MapLayer.from_geojson(p)
    assert len(loaded.features) == 2
    assert loaded.crs == "EPSG:4326"
print("GeoJSON save/load roundtrip OK")

layer.reproject("EPSG:3857")
assert layer.crs == "EPSG:3857"
print("reproject to EPSG:3857 OK")

# --- FieldListDialog: inline value editing and type coercion ---
layer2 = MapLayer.new_empty("Editor Test", "EPSG:4326", {"name": "text", "age": "number", "active": "boolean"})
f = layer2.add_feature("Person", "Point", [0, 0], {"name": "Person", "age": "42", "active": True})
fld = main.FieldListDialog(frame, layer2, feature=f)
assert fld.list_ctrl.GetItemCount() == 3
fld._select_field("age")
fld._set_value("age", "33")
assert f.properties["age"] == 33.0 and type(f.properties["age"]) is float
fld._set_value("active", "false")
assert f.properties["active"] is False
fld._set_value("name", "Renamed")
assert f.properties["name"] == "Renamed"
fld.refresh_list()
print("FieldListDialog inline value / type coercion OK")

fld2 = main.FieldListDialog(frame, layer2)
assert fld2.list_ctrl.GetItemCount() == 3
fld2.Destroy()
print("FieldListDialog no-feature mode OK")
fld.Destroy()

# --- frame helpers ---
frame.add_layer(layer2)
assert frame._layer_for_feature(f) is layer2
node = frame._find_item_node(frame.root_node, layer2)
assert node.IsOk()
frame.tree.SelectItem(node)
print("frame add_layer / tree node OK")

frame._clear_selection()
assert frame.current_layer is None
print("clear selection OK")

# --- undo / redo ---
frame.add_layer(layer)
assert len(frame.all_layers()) == 2
assert len(frame._undo_stack) == 2 and not frame._redo_stack
frame.on_undo(None)
assert len(frame.all_layers()) == 1
assert len(frame._redo_stack) == 1
frame.on_redo(None)
assert len(frame.all_layers()) == 2
assert not frame._redo_stack and len(frame._undo_stack) == 2
frame.add_layer(layer2)
assert len(frame.all_layers()) == 3
assert not frame._redo_stack
frame.on_undo(None)
assert len(frame.all_layers()) == 2
assert len(frame._redo_stack) == 1
print("undo / redo OK")

# --- export_web_map respects layer visibility ---
vis_a = MapLayer.new_empty("Visible A", "EPSG:4326", {"name": "text"})
vis_a.add_feature("PtA", "Point", [1, 1], {"name": "PtA"})
hidden_b = MapLayer.new_empty("Hidden B", "EPSG:4326", {"name": "text"})
hidden_b.add_feature("PtB", "Point", [2, 2], {"name": "PtB"})
hidden_b.visible = False
frame.add_layer(vis_a)
frame.add_layer(hidden_b)
frame.current_layer = vis_a
with tempfile.TemporaryDirectory() as td:
    p = os.path.join(td, "map.html")
    frame.export_web_map(p)
    html = open(p, encoding="utf-8").read()
    assert "Visible A" in html
    assert "Hidden B" not in html
hidden_b.visible = True
frame.current_layer = hidden_b
with tempfile.TemporaryDirectory() as td:
    p = os.path.join(td, "map2.html")
    frame.export_web_map(p)
    html = open(p, encoding="utf-8").read()
    assert "Hidden B" in html
print("export_web_map respects visibility OK")

# --- layer style defaults / set_style ---
styled = MapLayer.new_empty("Styled", "EPSG:4326", {"name": "text"})
styled.add_feature("A", "Point", [1, 1], {"name": "A"})
styled.add_feature("B", "Point", [2, 2], {"name": "B"})
assert styled.style_with_defaults() == DEFAULT_STYLE
styled.set_style({"color": "#FF0000", "marker": "star", "line_width": 3.0,
                  "fill_alpha": 0.8, "marker_size": 9.0})
s = styled.style_with_defaults()
assert s["color"] == "#FF0000" and s["marker"] == "star"
assert s["line_width"] == 3.0 and s["fill_alpha"] == 0.8 and s["marker_size"] == 9.0
styled.set_style({"color": "#00FF00"})
s = styled.style_with_defaults()
assert s["color"] == "#00FF00"
assert s["marker"] == DEFAULT_STYLE["marker"] and s["line_width"] == DEFAULT_STYLE["line_width"]
print("layer style defaults / set_style OK")

# --- LayerStyleDialog get_style + preview ---
sdlg = main.LayerStyleDialog(frame, styled)
st = sdlg.get_style()
assert set(st.keys()) == {"color", "line_width", "fill_alpha", "marker", "marker_size",
                          "style_mode", "attribute_field", "colormap", "class_count"}
assert st["color"] == "#00FF00" and st["marker"] == DEFAULT_STYLE["marker"]
assert st["style_mode"] == "single" and st["attribute_field"] == "" and st["class_count"] == 5
sdlg.color_ctrl.SetColour(wx.Colour(10, 20, 30))
sdlg.marker_ctrl.SetSelection(MARKER_CHOICES.index("square"))
st2 = sdlg.get_style()
assert st2["color"] == "#0A141E" and st2["marker"] == "square"
sdlg._on_change(None)
sdlg.Destroy()
print("LayerStyleDialog get_style OK")

# --- map canvas render + toggle ---
styled.add_feature("Road", "LineString", [[0, 0], [1, 1], [2, 0]],
                   {"name": "Road"})
styled.add_feature("Field", "Polygon", [[[5, 5], [6, 5], [6, 6], [5, 6]]],
                   {"name": "Field"})
frame.add_layer(styled)
frame.refresh_map()
assert frame.map_canvas.IsShown()
frame.map_pane_item.Check(False)
frame.on_toggle_map_pane(None)
assert not frame.map_canvas.IsShown()
frame.map_pane_item.Check(True)
frame.on_toggle_map_pane(None)
assert frame.map_canvas.IsShown()
frame.refresh_map()
print("map pane render + toggle OK")

# --- layer draw order (z-order) ---
order_a = MapLayer.new_empty("Z A", "EPSG:4326", {"name": "text"})
order_b = MapLayer.new_empty("Z B", "EPSG:4326", {"name": "text"})
order_a.add_feature("A", "Point", [1, 1], {"name": "A"})
order_b.add_feature("B", "Point", [2, 2], {"name": "B"})
frame.add_layer(order_a)
frame.add_layer(order_b)
assert frame.layers[-2:] == [order_a, order_b]
assert frame._move_layer(order_b, -1)
assert frame.layers[-2:] == [order_b, order_a]
while frame._move_layer(order_b, -1):
    pass
assert frame.layers[0] == order_b
assert frame._move_layer(order_b, -1) is False
assert frame._move_layer(order_b, 1)
while frame._move_layer(order_b, 1):
    pass
assert frame.layers[-1] == order_b
assert frame._move_layer(order_b, 1) is False
while frame._move_layer(order_a, 1):
    pass
frame._move_layer(order_b, 1)
assert frame.layers[-2:] == [order_a, order_b]
print("layer draw order (z-order) OK")

# --- legend control: populate, sync, toggle, move ---
frame.refresh_map()
legend = frame.map_canvas.legend_list
assert legend.GetItemCount() == len(frame.all_layers())
assert [l.name for l in frame.map_canvas._legend_layers] == [l.name for l in frame.all_layers()]
idx_a = frame.map_canvas._legend_layers.index(order_a)
frame.map_canvas.select_layer(order_a)
assert legend.GetFirstSelected() == idx_a
assert frame.map_canvas._selected_legend_layer() is order_a
order_b.visible = False
frame.refresh_map()
idx_b = frame.map_canvas._legend_layers.index(order_b)
assert legend.GetItemText(idx_b, 1) == "No"
assert not legend.IsItemChecked(idx_b)
order_b.visible = True
frame.refresh_map()
print("legend control populate + sync OK")


class _Evt:
    def __init__(self, index):
        self._i = index

    def GetIndex(self):
        return self._i


idx_b = frame.map_canvas._legend_layers.index(order_b)
legend.CheckItem(idx_b, False)
frame.map_canvas.on_legend_check(_Evt(idx_b))
assert order_b.visible is False
assert not legend.IsItemChecked(idx_b)
legend.Select(idx_b)
frame.map_canvas.on_legend_toggle(None)
assert order_b.visible is True
assert legend.IsItemChecked(idx_b)
print("legend visibility toggle OK")

frame.map_canvas.select_layer(order_a)
frame.map_canvas.on_legend_move_down(None)
assert frame.layers[-2:] == [order_b, order_a]
frame.map_canvas.on_legend_move_down(None)
assert frame.layers[-2:] == [order_b, order_a]
frame.map_canvas.on_legend_move_up(None)
assert frame.layers[-2:] == [order_a, order_b]
print("legend move buttons OK")

frame._move_layer(order_b, -1)
assert [l.name for l in frame.layers[-2:]] == ["Z B", "Z A"]
frame.on_undo(None)
assert [l.name for l in frame.layers[-2:]] == ["Z A", "Z B"]
frame.on_redo(None)
assert [l.name for l in frame.layers[-2:]] == ["Z B", "Z A"]
frame.on_undo(None)
assert [l.name for l in frame.layers[-2:]] == ["Z A", "Z B"]
print("z-order undo/redo OK")

# --- attribute-driven styling: graduated + categorised colour helpers ---
grad = MapLayer.new_empty("Grad", "EPSG:4326", {"name": "text", "pop": "number"})
for name, pop in (("A", 10), ("B", 30), ("C", 30), ("D", 100)):
    grad.add_feature(name, "Point", [0, 0], {"name": name, "pop": pop})
gstyle = {"style_mode": "graduated", "attribute_field": "pop",
          "colormap": "viridis", "class_count": 4}
gmap = main._graduated_colours(grad, gstyle)
assert set(gmap) == set(grad.features)
f = grad.features
assert gmap[f[0]] == gmap[f[1]] == gmap[f[2]]          # 10, 30, 30 -> class 0
assert gmap[f[3]] == main._graduated_palette("viridis", 4)[3]  # 100 -> last class
missing_field = MapLayer.new_empty("NoField", "EPSG:4326", {"name": "text", "pop": "number"})
missing_field.add_feature("X", "Point", [0, 0], {"name": "X"})
assert main._graduated_colours(missing_field, gstyle) == {}
cstyle = {"style_mode": "categorised", "attribute_field": "name", "colormap": "viridis"}
cmap = main._categorised_colours(grad, cstyle)
assert len({cmap[g] for g in grad.features}) == 4
assert cmap[grad.features[0]] != cmap[grad.features[1]]
assert main._feature_colour_map(grad, {"style_mode": "single"}) == {}
print("graduated + categorised colour helpers OK")

# --- LayerStyleDialog graduated mode get_style ---
sdlg2 = main.LayerStyleDialog(frame, grad)
sdlg2.mode_ctrl.SetSelection(1)
sdlg2.field_ctrl.SetStringSelection("pop")
sdlg2.cmap_ctrl.SetStringSelection("plasma")
sdlg2.classes_ctrl.SetValue(3)
sdlg2._update_enabled()
g = sdlg2.get_style()
assert g["style_mode"] == "graduated"
assert g["attribute_field"] == "pop" and g["colormap"] == "plasma" and g["class_count"] == 3
assert not sdlg2.color_ctrl.IsEnabled() and sdlg2.field_ctrl.IsEnabled()
sdlg2._on_change(None)
sdlg2.Destroy()
print("LayerStyleDialog graduated get_style OK")

# --- RasterStyleDialog get_style + colormap persistence ---
rlyr = TerrainLayer.new_empty("RTerrain", "EPSG:4326", width=8, height=8)
rsdlg = main.RasterStyleDialog(frame, rlyr)
rsdlg.cmap_ctrl.SetStringSelection("coolwarm")
style_out = rsdlg.get_style()
assert style_out["colormap"] == "coolwarm"
assert style_out["render_mode"] == "single"
rsdlg._draw_preview()
rsdlg.Destroy()
rlyr.set_style({"colormap": "coolwarm"})
assert rlyr.style_with_defaults()["colormap"] == "coolwarm"
assert main._legend_colour(rlyr) == main._sample_colormap("coolwarm", 0.5)
print("RasterStyleDialog get_style + persistence OK")

# --- legend colour reflects attribute styling ---
frame.add_layer(grad)
frame.refresh_map()
legend = frame.map_canvas.legend_list
idx_grad = frame.map_canvas._legend_layers.index(grad)
assert legend.GetItemTextColour(idx_grad) == wx.Colour(main._legend_colour(grad))
grad.set_style({"style_mode": "graduated", "attribute_field": "pop",
                "colormap": "viridis", "class_count": 4})
frame.refresh_map()
idx_grad = frame.map_canvas._legend_layers.index(grad)
assert legend.GetItemTextColour(idx_grad) == wx.Colour(main._legend_colour(grad))
print("legend colour for attribute-styled layer OK")

frame.Destroy()
print("ALL SMOKE TESTS PASSED")
