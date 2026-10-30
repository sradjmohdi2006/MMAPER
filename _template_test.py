import json
import os
import tempfile
import wx
import main
import data_model
from data_model import (FIELD_TEMPLATES, MapLayer, TEMPLATE_FILE, TEMPLATE_FIELD_TYPES,
                        TEMPLATE_VERSION, apply_feature_template, build_feature_template,
                        build_layer_template, create_layer_from_template, load_custom_templates,
                        save_custom_templates, template_from_json, template_from_json_string,
                        template_to_json, template_to_json_string, validate_template)

app = wx.App(False)
frame = main.MainFrame()

# --- built-in presets ---
assert len(FIELD_TEMPLATES) >= 20, f"expected 20+ presets, got {len(FIELD_TEMPLATES)}"
for name, fields in FIELD_TEMPLATES.items():
    assert isinstance(fields, dict), f"preset '{name}' is not a dict"
    if name != "Blank":
        assert fields, f"preset '{name}' empty"
    for ftype in fields.values():
        assert ftype in TEMPLATE_FIELD_TYPES, f"preset '{name}' has non-canonical type {ftype}"
assert "Health Services" in FIELD_TEMPLATES and "Security & Defence" in FIELD_TEMPLATES
print("Field template presets OK")

# --- build layer template ---
layer = MapLayer.new_empty("Census", "EPSG:4326", {"name": "text", "pop": "integer"})
layer.add_feature("City A", "Point", [30, 50], {"name": "City A", "pop": 100})
layer.add_feature("City B", "Point", [31, 51], {"name": "City B", "pop": 200})
layer.add_property("country", "France", "text")
layer.set_style({"color": "#FF0000", "marker": "star"})
ltpl = build_layer_template(layer, "Census Tpl", "Population census")
assert ltpl["mmapper_template"] == TEMPLATE_VERSION
assert ltpl["kind"] == "layer" and ltpl["name"] == "Census Tpl"
assert {f["name"]: f["type"] for f in ltpl["fields"]} == {"name": "text", "pop": "integer"}
props = {p["name"]: (p["type"], p["value"]) for p in ltpl["properties"]}
assert props == {"country": ("text", "France")}
assert ltpl["crs"] == "EPSG:4326"
assert ltpl["style"]["color"] == "#FF0000" and ltpl["style"]["marker"] == "star"
print("build_layer_template OK")

# --- build feature template ---
ftpl = build_feature_template(layer, layer.features[0], "City A Tpl", "One city")
assert ftpl["kind"] == "feature" and ftpl["name"] == "City A Tpl"
fld = {f["name"]: (f["type"], f["default"]) for f in ftpl["fields"]}
assert fld["pop"] == ("integer", 100) and fld["name"] == ("text", "City A")
print("build_feature_template OK")

# --- validate_template normalization ---
norm = validate_template({"mmapper_template": TEMPLATE_VERSION, "kind": "layer", "name": "X",
                          "fields": [{"name": "a", "type": "NUMBER", "default": "5"},
                                     {"name": "a", "type": "text"},
                                     {"name": "b", "type": "weird"}]})
assert [f["name"] for f in norm["fields"]] == ["a", "b"]          # duplicate dropped
assert norm["fields"][0]["type"] == "number"                      # canonicalized
assert norm["fields"][0]["default"] == 5.0                        # coerced
assert norm["fields"][1]["type"] == "text"                        # unknown type -> text
legacy = validate_template({"name": "text", "pop": "integer"})    # legacy dict wrapped
assert legacy["kind"] == "layer" and legacy["fields"][0]["name"] == "name"
try:
    validate_template({"mmapper_template": TEMPLATE_VERSION, "kind": "bogus"})
    raise SystemExit("expected ValueError for bad kind")
except ValueError:
    pass
try:
    validate_template("not a dict")
    raise SystemExit("expected ValueError for non-dict")
except ValueError:
    pass
print("validate_template normalization OK")

# --- JSON string / file roundtrip ---
s = template_to_json_string(ltpl)
parsed = template_from_json_string(s)
assert parsed == validate_template(ltpl)
with tempfile.TemporaryDirectory() as td:
    p = os.path.join(td, "layer_tpl.json")
    template_to_json(ltpl, p)
    loaded = template_from_json(p)
    assert loaded == validate_template(ltpl)
    with open(p, encoding="utf-8") as f:
        raw = json.load(f)
    assert raw["mmapper_template"] == TEMPLATE_VERSION and raw["kind"] == "layer"
    print("JSON string/file roundtrip OK")

    bad = os.path.join(td, "bad.json")
    with open(bad, "w", encoding="utf-8") as f:
        json.dump({"no_marker": True}, f)
    try:
        template_from_json(bad)
        raise SystemExit("expected ValueError for missing marker")
    except ValueError:
        pass
    print("missing marker raises ValueError OK")

# --- create_layer_from_template ---
cloned = create_layer_from_template(ltpl, name="Imported Census", crs="EPSG:3857")
assert cloned.name == "Imported Census" and cloned.crs == "EPSG:3857"
assert cloned.schema == {"name": "text", "pop": "integer"}
assert cloned.properties.get("country") == "France"
assert cloned.style_with_defaults()["color"] == "#FF0000"
assert cloned.style_with_defaults()["marker"] == "star"
try:
    create_layer_from_template(ftpl)
    raise SystemExit("expected ValueError creating layer from feature template")
except ValueError:
    pass
print("create_layer_from_template OK")

# --- apply_feature_template ---
target = MapLayer.new_empty("Target", "EPSG:4326", {})
f = target.add_feature("P", "Point", [0, 0], {"name": "P"})
added = apply_feature_template(target, f, ftpl)
assert added == 2
assert "pop" in target.schema and f.properties.get("pop") == 100
assert f.properties.get("name") == "P"           # existing value preserved
added2 = apply_feature_template(target, f, ftpl)
assert added2 == 0                                # idempotent
try:
    apply_feature_template(target, f, ltpl)
    raise SystemExit("expected ValueError applying layer template to feature")
except ValueError:
    pass
print("apply_feature_template OK")

# --- coercion of defaults ---
co = validate_template({"mmapper_template": TEMPLATE_VERSION, "kind": "layer", "name": "C",
                        "fields": [{"name": "i", "type": "integer", "default": "7"},
                                   {"name": "n", "type": "number", "default": "2.5"},
                                   {"name": "b", "type": "boolean", "default": "yes"},
                                   {"name": "b2", "type": "boolean", "default": 0}]})
fmap = {f["name"]: (f["type"], f["default"]) for f in co["fields"]}
assert fmap["i"] == ("integer", 7)
assert fmap["n"] == ("number", 2.5)
assert fmap["b"] == ("boolean", True) and fmap["b2"] == ("boolean", False)
print("default coercion OK")

# --- custom template library persistence (isolated file) ---
real_tpl_file = TEMPLATE_FILE
with tempfile.TemporaryDirectory() as td:
    data_model.TEMPLATE_FILE = os.path.join(td, "mmapper_templates.json")
    save_custom_templates({"Census Tpl": ltpl, "City A Tpl": ftpl})
    lib = load_custom_templates()
    assert "Census Tpl" in lib and "City A Tpl" in lib
    assert lib["Census Tpl"]["kind"] == "layer"
    assert lib["City A Tpl"]["kind"] == "feature"
    # file content is standardized (cross-device)
    with open(data_model.TEMPLATE_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    assert "Census Tpl" in raw and raw["Census Tpl"]["mmapper_template"] == TEMPLATE_VERSION
    # roundtrip: file load -> create layer
    again = create_layer_from_template(raw["Census Tpl"])
    assert again.schema == {"name": "text", "pop": "integer"}
    print("custom template library save/load roundtrip OK")
data_model.TEMPLATE_FILE = real_tpl_file

# --- cross-device deterministic output ---
s1 = template_to_json_string(ltpl)
s2 = template_to_json_string(validate_template(ltpl))
assert s1 == s2
print("deterministic template output OK")

# --- NewLayerDialog: custom template in choices + apply ---
data_model.TEMPLATE_FILE = TEMPLATE_FILE  # library may not exist; load returns {}
ndlg = main.NewLayerDialog(frame)
assert ndlg.custom_templates == load_custom_templates()
names = [ndlg.template_ctrl.GetString(i) for i in range(ndlg.template_ctrl.GetCount())]
assert "Demographics" in names and "Blank" in names
ndlg.template_ctrl.SetStringSelection("Demographics")
ndlg.on_apply_template(None)
assert ndlg.fields_ctrl.GetItemCount() == len(FIELD_TEMPLATES["Demographics"])
fields = ndlg.get_data()[2]
assert fields == FIELD_TEMPLATES["Demographics"]
ndlg.Destroy()
print("NewLayerDialog built-in template OK")

# --- SingleFieldDialog: integer type ---
sdlg = main.SingleFieldDialog(frame, ftype="integer", default="5")
assert sdlg.get_data()[1] == "integer"
assert "integer" in [sdlg.type_ctrl.GetString(i) for i in range(sdlg.type_ctrl.GetCount())]
sdlg.Destroy()
print("SingleFieldDialog integer type OK")

# --- TemplateDialog: merged template fields ---
tdlg = main.TemplateDialog(frame)
assert tdlg.custom_templates == load_custom_templates()
tdlg.template_ctrl.SetStringSelection("Demographics")
tdlg.update_preview()
assert tdlg.get_template_fields() == FIELD_TEMPLATES["Demographics"]
assert "population" in tdlg.preview.GetLabel()
tdlg.Destroy()
print("TemplateDialog merged fields OK")

# --- TemplateNameDialog ---
nd = main.TemplateNameDialog(frame, name="My Tpl", description="desc")
nd.name_ctrl.SetValue("Renamed")
assert nd.get_data() == ("Renamed", "desc")
nd.Destroy()
print("TemplateNameDialog OK")

# --- frame handlers: save/import template end to end ---
class _FakeNameDialog:
    def __init__(self, *a, **k):
        pass

    def ShowModal(self):
        return wx.ID_OK

    def get_data(self):
        return "Auto Feature", "saved by test"

    def Destroy(self):
        pass


class _FakeFileDialog:
    path = ""

    def __init__(self, *a, **k):
        pass

    def ShowModal(self):
        return wx.ID_OK

    def GetPath(self):
        return _FakeFileDialog.path

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakePositionDialog:
    def __init__(self, *a, **k):
        pass

    def ShowModal(self):
        return wx.ID_OK

    def is_child(self):
        return False

    def Destroy(self):
        pass


orig_name_dlg = main.TemplateNameDialog
orig_file_dlg = wx.FileDialog
orig_position_dlg = main.PositionDialog
main.TemplateNameDialog = _FakeNameDialog
wx.FileDialog = _FakeFileDialog
main.PositionDialog = _FakePositionDialog
try:
    frame.add_layer(layer)
    frame.current_layer = layer
    frame.current_feature = layer.features[0]
    with tempfile.TemporaryDirectory() as td:
        data_model.TEMPLATE_FILE = os.path.join(td, "mmapper_templates.json")
        frame.on_save_custom_template(None)  # feature selected -> saves feature template
        lib = load_custom_templates()
        assert len(lib) == 1
        name = next(iter(lib))
        assert lib[name]["kind"] == "feature"
        # import a layer template from JSON -> creates a new layer
        tp = os.path.join(td, "layer_tpl.json")
        template_to_json(ltpl, tp)
        _FakeFileDialog.path = tp
        old_count = len(frame.layers)
        frame.on_import_template(None)
        assert len(frame.layers) == old_count + 1
        new_layer = frame.layers[-1]
        assert isinstance(new_layer, MapLayer)
        assert new_layer.schema == {"name": "text", "pop": "integer"}
        frame.on_undo(None)
        assert len(frame.layers) == old_count
        frame.on_redo(None)
        assert len(frame.layers) == old_count + 1
        frame.on_undo(None)
finally:
    main.TemplateNameDialog = orig_name_dlg
    wx.FileDialog = orig_file_dlg
    main.PositionDialog = orig_position_dlg
    data_model.TEMPLATE_FILE = real_tpl_file
print("frame save/import template handlers OK")

frame.Destroy()
print("ALL TEMPLATE TESTS PASSED")
