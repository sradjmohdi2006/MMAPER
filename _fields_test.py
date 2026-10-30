import wx
import main
from data_model import Layer, MapLayer

app = wx.App(False)
frame = main.MainFrame()

# --- _parse_number: tolerant unit-suffix parsing ---
assert main._parse_number("123.45 m\u00b2") == 123.45
assert main._parse_number("1,234 km\u00b2") == 1234.0
assert main._parse_number("-0.5") == -0.5
assert main._parse_number("2e3") == 2000.0
assert main._parse_number(".5") == 0.5
assert main._parse_number("5 units") == 5.0
assert main._parse_number("  42  ") == 42.0
assert main._parse_number(7) == 7.0
for bad in ("abc", "", "  ", "m\u00b2", "true"):
    try:
        main._parse_number(bad)
        raise SystemExit(f"expected ValueError for {bad!r}")
    except ValueError:
        pass
try:
    main._parse_number(True)
    raise SystemExit("expected ValueError for bool")
except ValueError:
    pass
print("_parse_number unit parsing OK")

# --- dialog fixture ---
layer = MapLayer.new_empty("Land", "EPSG:4326",
                           {"name": "text", "area": "number", "buildings": "integer",
                            "protected": "boolean"})
f1 = layer.add_feature("Parcel 1", "Polygon", [[[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]],
                       {"name": "Parcel 1", "area": 100.0, "buildings": 2, "protected": True})
f2 = layer.add_feature("Parcel 2", "Polygon", [[[20, 20], [20, 30], [30, 30], [30, 20], [20, 20]]],
                       {"name": "Parcel 2", "area": 150.0, "buildings": 5, "protected": False})
assert layer.schema["area"] == "number"

# --- dialog opens for the feature passed from the tree ---
dlg = main.FieldListDialog(frame, layer, f1)
assert dlg.feature_ctrl is not None
assert dlg.feature is f1
assert dlg.feature_ctrl.GetSelection() == 0
assert dlg._props() is f1.properties
names = [dlg.list_ctrl.GetItemText(i, 0) for i in range(dlg.list_ctrl.GetItemCount())]
assert "area" in names and "buildings" in names
vals = {dlg.list_ctrl.GetItemText(i, 0): dlg.list_ctrl.GetItemText(i, 1)
        for i in range(dlg.list_ctrl.GetItemCount())}
assert vals["area"] == "100.0" and vals["protected"] == "True"
dlg.Destroy()
print("FieldListDialog with a feature OK")

# --- no feature passed: first feature auto-selected, values editable ---
dlg = main.FieldListDialog(frame, layer, None)
assert dlg.feature is f1
assert dlg.feature_ctrl.GetSelection() == 0
assert dlg._props() is f1.properties
dlg.Destroy()
print("FieldListDialog auto-selects first feature OK")

# --- switch feature from the combo ---
dlg = main.FieldListDialog(frame, layer, None)
dlg.feature_ctrl.SetSelection(1)
dlg._on_feature_changed(None)
assert dlg.feature is f2
assert dlg._props() is f2.properties
vals = {dlg.list_ctrl.GetItemText(i, 0): dlg.list_ctrl.GetItemText(i, 1)
        for i in range(dlg.list_ctrl.GetItemCount())}
assert vals["area"] == "150.0"
dlg.Destroy()
print("FieldListDialog feature switching OK")

# --- _set_value: tolerant parsing for number/integer, unchanged for others ---
dlg = main.FieldListDialog(frame, layer, f1)
dlg._set_value("area", "120 m\u00b2")
assert f1.properties["area"] == 120.0
dlg._set_value("area", "1,250 km\u00b2")
assert f1.properties["area"] == 1250.0
dlg._set_value("area", "12.5")
assert f1.properties["area"] == 12.5
dlg._set_value("area", "unknown")
assert f1.properties["area"] == "unknown"
dlg._set_value("buildings", "7 units")
assert f1.properties["buildings"] == 7
dlg._set_value("protected", "yes")
assert f1.properties["protected"] is True
dlg._set_value("name", "Parcel 1 renamed")
assert f1.properties["name"] == "Parcel 1 renamed"
assert isinstance(f1.properties["buildings"], int)
dlg.Destroy()
print("FieldListDialog _set_value unit handling OK")

# --- empty layer: no feature available ---
empty = MapLayer.new_empty("Empty", "EPSG:4326", {"name": "text", "area": "number"})
dlg = main.FieldListDialog(frame, empty, None)
assert dlg.feature_ctrl is not None
assert dlg.feature is None
assert dlg._props() is None
dlg.Destroy()
print("FieldListDialog empty layer OK")

# --- group mode: no feature picker ---
group = Layer("Group")
group.add_property("temperature", 20.0, "number")
dlg = main.FieldListDialog(frame, None, None, group=group)
assert dlg.feature_ctrl is None
assert dlg._props() is group.properties
dlg.Destroy()
print("FieldListDialog group mode OK")


# --- on_fields_list: works when only a feature is selected ---
class _FakeFieldsDialog:
    instances = []

    def __init__(self, *a, **k):
        self.parent = a[0]
        self.layer = a[1]
        self.feature = a[2] if len(a) > 2 else None
        _FakeFieldsDialog.instances.append(self)

    def ShowModal(self):
        return wx.ID_OK

    def Destroy(self):
        pass


orig = main.FieldListDialog
_FakeFieldsDialog.instances = []
main.FieldListDialog = _FakeFieldsDialog
try:
    frame.add_layer(layer)
    frame.current_layer = None            # only the feature is selected in the tree
    frame.current_feature = f2
    frame.on_fields_list(None)
    assert isinstance(frame.layers[-1], MapLayer)  # still there after refresh
    assert len(_FakeFieldsDialog.instances) == 1
    assert _FakeFieldsDialog.instances[0].layer is layer
    assert _FakeFieldsDialog.instances[0].feature is f2
except Exception:
    main.FieldListDialog = orig
    raise
main.FieldListDialog = orig
print("on_fields_list with feature selected OK")

# --- on_fields_list: layer only still opens with the feature picker ---
dlg = main.FieldListDialog(frame, layer, None)
assert dlg.feature is f1 and dlg.feature_ctrl is not None
dlg.Destroy()
print("on_fields_list layer-only flow OK")

frame.Destroy()
print("ALL FIELDS TESTS PASSED")
