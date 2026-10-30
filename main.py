import copy
import json
import math
import os
import re

import wx

from data_model import (COLORMAP_CHOICES, FIELD_TEMPLATES, MARKER_CHOICES, Feature, Layer,
                        MapLayer, SpatialAnalysisEngine, TerrainLayer, apply_feature_template,
                        build_feature_template, build_layer_template, create_layer_from_template,
                        load_custom_templates, load_project, save_custom_templates, save_project,
                        template_from_json, template_to_json)

_MARKER_TO_MPL = {
    "circle": "o",
    "square": "s",
    "triangle": "^",
    "star": "*",
    "plus": "+",
    "x": "x",
}

_NUMBER_LEAD_RE = re.compile(r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)")


def _parse_number(text):
    if isinstance(text, bool):
        raise ValueError
    if isinstance(text, (int, float)):
        return float(text)
    t = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", str(text).strip())
    m = _NUMBER_LEAD_RE.match(t)
    if not m:
        raise ValueError
    return float(m.group(1))


def _feature_lonlat(layer, name):
    """Return the lon/lat of a named feature's point (or centroid) in WGS84."""
    feature = next((f for f in layer.features if f.get_name() == name), None)
    if feature is None or not feature.geometry:
        return None
    from shapely.geometry import shape
    geom = shape(feature.geometry)
    if geom.is_empty:
        return None
    try:
        import geopandas as gpd
        if layer.crs:
            gdf = gpd.GeoDataFrame(geometry=[geom], crs=layer.crs)
            if str(gdf.crs) != "EPSG:4326":
                gdf = gdf.to_crs("EPSG:4326")
            geom = gdf.geometry.iloc[0]
    except Exception:
        pass
    if geom.geom_type == "Point":
        return (float(geom.x), float(geom.y))
    centroid = geom.centroid
    return (float(centroid.x), float(centroid.y))


def _rgb_to_hex(rgba):
    r = max(0, min(255, int(round(rgba[0] * 255))))
    g = max(0, min(255, int(round(rgba[1] * 255))))
    b = max(0, min(255, int(round(rgba[2] * 255))))
    return f"#{r:02X}{g:02X}{b:02X}"


def _decimate_render(array, max_dim=1024):
    shape = array.shape
    h, w = shape[0], shape[1]
    if h <= max_dim and w <= max_dim:
        return array
    row_step = max(1, math.ceil(h / max_dim))
    col_step = max(1, math.ceil(w / max_dim))
    return array[::row_step, ::col_step]


def _get_cmap(name):
    import matplotlib
    name = name or "viridis"
    get = getattr(matplotlib.cm, "get_cmap", None)
    if get is not None:
        try:
            return get(name)
        except Exception:
            return get("viridis")
    try:
        return matplotlib.colormaps[name]
    except Exception:
        return matplotlib.colormaps["viridis"]


def _sample_colormap(name, t):
    return _rgb_to_hex(_get_cmap(name)(float(t)))


def _graduated_palette(cmap_name, n):
    cmap = _get_cmap(cmap_name)
    return [_rgb_to_hex(cmap((i + 0.5) / n)) for i in range(n)]


def _categorical_palette(n):
    cmap = _get_cmap("tab10")
    return [_rgb_to_hex(cmap(i % 10)) for i in range(n)]


def _graduated_colours(layer, style):
    field = style.get("attribute_field") or ""
    values = []
    for f in layer.features:
        v = f.properties.get(field)
        try:
            values.append((f, float(v)))
        except (TypeError, ValueError):
            values.append((f, None))
    valid = [v for _, v in values if v is not None]
    if not valid:
        return {}
    n = max(2, int(style.get("class_count", 5)))
    palette = _graduated_palette(style.get("colormap", "viridis"), n)
    vmin, vmax = min(valid), max(valid)
    span = (vmax - vmin) or 1.0
    colours = {}
    for f, v in values:
        if v is None:
            colours[f] = None
        else:
            cls = min(int((v - vmin) / (span / n)), n - 1)
            colours[f] = palette[cls]
    return colours


def _categorised_colours(layer, style):
    field = style.get("attribute_field") or ""
    categories = []
    for f in layer.features:
        v = f.properties.get(field)
        if v is not None and str(v) not in categories:
            categories.append(str(v))
    palette = _categorical_palette(len(categories))
    colours = {}
    for f in layer.features:
        v = f.properties.get(field)
        if v is None:
            colours[f] = None
        else:
            colours[f] = palette[categories.index(str(v))]
    return colours


def _feature_colour_map(layer, style):
    mode = style.get("style_mode", "single")
    if mode == "graduated":
        return _graduated_colours(layer, style)
    if mode == "categorised":
        return _categorised_colours(layer, style)
    return {}


def _legend_colour(layer):
    style = layer.style_with_defaults()
    if isinstance(layer, TerrainLayer):
        return _sample_colormap(style.get("colormap", "terrain"), 0.5)
    if isinstance(layer, MapLayer) and style.get("style_mode") != "single":
        colours = _feature_colour_map(layer, style)
        for f in layer.features:
            if colours.get(f):
                return colours[f]
    return style["color"]


class DetailDialog(wx.Dialog):
    def __init__(self, parent, title, key, value):
        super().__init__(parent, title=title, size=(400, 300))

        sizer = wx.BoxSizer(wx.VERTICAL)

        label = wx.StaticText(self, label=f"Property: {key}")
        sizer.Add(label, 0, wx.ALL | wx.EXPAND, 10)

        self.text_ctrl = wx.TextCtrl(self, style=wx.TE_MULTILINE, value=str(value))
        sizer.Add(self.text_ctrl, 1, wx.ALL | wx.EXPAND, 10)

        btn_sizer = wx.StdDialogButtonSizer()
        ok_btn = wx.Button(self, wx.ID_OK)
        cancel_btn = wx.Button(self, wx.ID_CANCEL)
        btn_sizer.AddButton(ok_btn)
        btn_sizer.AddButton(cancel_btn)
        btn_sizer.Realize()
        sizer.Add(btn_sizer, 0, wx.ALL | wx.EXPAND, 10)

        self.SetSizer(sizer)

    def get_value(self):
        return self.text_ctrl.GetValue()


class PropertyDialog(wx.Dialog):
    def __init__(self, parent, title, key="", value=""):
        super().__init__(parent, title=title, size=(400, 260))

        sizer = wx.BoxSizer(wx.VERTICAL)
        grid = wx.GridSizer(2, 2, 5, 5)

        grid.Add(wx.StaticText(self, label="Property"))
        self.key_ctrl = wx.TextCtrl(self, value=key)
        grid.Add(self.key_ctrl)

        grid.Add(wx.StaticText(self, label="Value"))
        self.value_ctrl = wx.TextCtrl(self, value=str(value))
        grid.Add(self.value_ctrl)

        sizer.Add(grid, 0, wx.ALL | wx.EXPAND, 10)

        btn_sizer = wx.StdDialogButtonSizer()
        ok_btn = wx.Button(self, wx.ID_OK)
        cancel_btn = wx.Button(self, wx.ID_CANCEL)
        btn_sizer.AddButton(ok_btn)
        btn_sizer.AddButton(cancel_btn)
        btn_sizer.Realize()
        sizer.Add(btn_sizer, 0, wx.ALL | wx.EXPAND, 10)

        self.SetSizer(sizer)

    def get_data(self):
        return self.key_ctrl.GetValue(), self.value_ctrl.GetValue()


class SingleFieldDialog(wx.Dialog):
    def __init__(self, parent, title="Add Field", name="", ftype="text", default=""):
        super().__init__(parent, title=title, size=(380, 190))

        sizer = wx.BoxSizer(wx.VERTICAL)
        grid = wx.GridSizer(3, 2, 5, 5)

        grid.Add(wx.StaticText(self, label="Field Name"))
        self.name_ctrl = wx.TextCtrl(self, value=name)
        grid.Add(self.name_ctrl)

        grid.Add(wx.StaticText(self, label="Type"))
        self.type_ctrl = wx.Choice(self, choices=["text", "number", "integer", "boolean"])
        if ftype in ("text", "number", "integer", "boolean"):
            self.type_ctrl.SetStringSelection(ftype)
        else:
            self.type_ctrl.SetSelection(0)
        grid.Add(self.type_ctrl)

        grid.Add(wx.StaticText(self, label="Default Value"))
        self.default_ctrl = wx.TextCtrl(self, value=default)
        grid.Add(self.default_ctrl)

        sizer.Add(grid, 0, wx.ALL | wx.EXPAND, 10)

        btn_sizer = wx.StdDialogButtonSizer()
        ok_btn = wx.Button(self, wx.ID_OK)
        cancel_btn = wx.Button(self, wx.ID_CANCEL)
        btn_sizer.AddButton(ok_btn)
        btn_sizer.AddButton(cancel_btn)
        btn_sizer.Realize()
        sizer.Add(btn_sizer, 0, wx.ALL | wx.EXPAND, 10)

        self.SetSizer(sizer)
        self.name_ctrl.SetFocus()

    def get_data(self):
        name = self.name_ctrl.GetValue().strip()
        ftype = self.type_ctrl.GetStringSelection()
        default = self.default_ctrl.GetValue().strip()
        return name, ftype, default


class NewLayerDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="New GeoJSON Layer", size=(540, 480))
        self.custom_templates = load_custom_templates()

        sizer = wx.BoxSizer(wx.VERTICAL)
        grid = wx.GridSizer(5, 2, 5, 5)

        grid.Add(wx.StaticText(self, label="Layer Name"))
        self.name_ctrl = wx.TextCtrl(self)
        grid.Add(self.name_ctrl)

        grid.Add(wx.StaticText(self, label="CRS"))
        self.crs_ctrl = wx.Choice(self, choices=["EPSG:4326", "EPSG:3857", "EPSG:32633", "EPSG:3395"])
        self.crs_ctrl.SetSelection(0)
        grid.Add(self.crs_ctrl)

        grid.Add(wx.StaticText(self, label="Field Template"))
        template_row = wx.BoxSizer(wx.HORIZONTAL)
        template_names = list(FIELD_TEMPLATES.keys())
        for cname in self.custom_templates:
            if cname not in template_names:
                template_names.append(cname)
        self.template_ctrl = wx.Choice(self, choices=template_names)
        self.template_ctrl.SetSelection(0)
        apply_tpl_btn = wx.Button(self, label="Apply Template")
        template_row.Add(self.template_ctrl, 1, wx.EXPAND)
        template_row.Add(apply_tpl_btn, 0, wx.LEFT, 5)
        grid.Add(template_row)

        sizer.Add(grid, 0, wx.ALL | wx.EXPAND, 10)

        sizer.Add(wx.StaticText(self, label="Fields (schema):"), 0, wx.LEFT | wx.TOP, 10)
        self.fields_ctrl = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.fields_ctrl.InsertColumn(0, "Field", width=220)
        self.fields_ctrl.InsertColumn(1, "Type", width=140)
        sizer.Add(self.fields_ctrl, 1, wx.ALL | wx.EXPAND, 10)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        add_btn = wx.Button(self, label="Add Field")
        remove_btn = wx.Button(self, label="Remove Field")
        btn_row.Add(add_btn)
        btn_row.Add(remove_btn, 0, wx.LEFT, 5)
        sizer.Add(btn_row, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        btn_sizer = wx.StdDialogButtonSizer()
        ok_btn = wx.Button(self, wx.ID_OK)
        cancel_btn = wx.Button(self, wx.ID_CANCEL)
        btn_sizer.AddButton(ok_btn)
        btn_sizer.AddButton(cancel_btn)
        btn_sizer.Realize()
        sizer.Add(btn_sizer, 0, wx.ALL | wx.EXPAND, 10)

        self.SetSizer(sizer)

        add_btn.Bind(wx.EVT_BUTTON, self.on_add_field)
        remove_btn.Bind(wx.EVT_BUTTON, self.on_remove_field)
        apply_tpl_btn.Bind(wx.EVT_BUTTON, self.on_apply_template)

    def _template_fields(self, name):
        if name in FIELD_TEMPLATES:
            return FIELD_TEMPLATES[name]
        tpl = self.custom_templates.get(name)
        if tpl:
            return {f["name"]: f["type"] for f in tpl.get("fields", [])}
        return {}

    def on_apply_template(self, event):
        self.fields_ctrl.DeleteAllItems()
        name = self.template_ctrl.GetStringSelection()
        for fname, ftype in self._template_fields(name).items():
            idx = self.fields_ctrl.GetItemCount()
            self.fields_ctrl.InsertItem(idx, fname)
            self.fields_ctrl.SetItem(idx, 1, ftype)

    def on_add_field(self, event):
        dlg = SingleFieldDialog(self)
        if dlg.ShowModal() == wx.ID_OK:
            name, ftype, _ = dlg.get_data()
            if name:
                idx = self.fields_ctrl.GetItemCount()
                self.fields_ctrl.InsertItem(idx, name)
                self.fields_ctrl.SetItem(idx, 1, ftype)
        dlg.Destroy()

    def on_remove_field(self, event):
        idx = self.fields_ctrl.GetNextSelected(-1)
        if idx >= 0:
            self.fields_ctrl.DeleteItem(idx)

    def get_data(self):
        name = self.name_ctrl.GetValue().strip() or "New Layer"
        crs = self.crs_ctrl.GetStringSelection()
        fields = {}
        for i in range(self.fields_ctrl.GetItemCount()):
            fname = self.fields_ctrl.GetItemText(i, 0)
            ftype = self.fields_ctrl.GetItemText(i, 1)
            fields[fname] = ftype
        return name, crs, fields


class NewRasterDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="New Terrain/Raster Layer", size=(480, 380))
        sizer = wx.BoxSizer(wx.VERTICAL)
        grid = wx.FlexGridSizer(7, 2, 6, 8)
        grid.AddGrowableCol(1)

        grid.Add(wx.StaticText(self, label="Layer Name"))
        self.name_ctrl = wx.TextCtrl(self)
        grid.Add(self.name_ctrl)

        grid.Add(wx.StaticText(self, label="CRS"))
        self.crs_ctrl = wx.Choice(self, choices=["EPSG:4326", "EPSG:3857", "EPSG:3395", "EPSG:32633"])
        self.crs_ctrl.SetSelection(0)
        grid.Add(self.crs_ctrl)

        grid.Add(wx.StaticText(self, label="Width (cells)"))
        self.width_ctrl = wx.TextCtrl(self, value="100")
        grid.Add(self.width_ctrl)

        grid.Add(wx.StaticText(self, label="Height (cells)"))
        self.height_ctrl = wx.TextCtrl(self, value="100")
        grid.Add(self.height_ctrl)

        grid.Add(wx.StaticText(self, label="Bounds (left, bottom, right, top)"))
        self.bounds_ctrl = wx.TextCtrl(self)
        self.bounds_ctrl.SetHint("blank = 0 to width, 0 to height")
        grid.Add(self.bounds_ctrl)

        grid.Add(wx.StaticText(self, label="Starting value"))
        self.value_ctrl = wx.TextCtrl(self, value="0")
        grid.Add(self.value_ctrl)

        grid.Add(wx.StaticText(self, label="Number of bands"))
        self.bands_ctrl = wx.TextCtrl(self, value="1")
        grid.Add(self.bands_ctrl)

        sizer.Add(grid, 0, wx.ALL | wx.EXPAND, 10)

        btn_sizer = wx.StdDialogButtonSizer()
        ok_btn = wx.Button(self, wx.ID_OK)
        cancel_btn = wx.Button(self, wx.ID_CANCEL)
        btn_sizer.AddButton(ok_btn)
        btn_sizer.AddButton(cancel_btn)
        btn_sizer.Realize()
        sizer.Add(btn_sizer, 0, wx.ALL | wx.EXPAND, 10)

        self.SetSizer(sizer)

    def get_data(self):
        name = self.name_ctrl.GetValue().strip() or "New Terrain"
        crs = self.crs_ctrl.GetStringSelection()
        try:
            width = max(1, int(self.width_ctrl.GetValue().strip() or "100"))
            height = max(1, int(self.height_ctrl.GetValue().strip() or "100"))
        except ValueError:
            width, height = 100, 100
        bounds = None
        text = self.bounds_ctrl.GetValue().strip()
        if text:
            parts = [p.strip() for p in text.replace(";", ",").split(",")]
            if len(parts) == 4:
                try:
                    bounds = tuple(float(p) for p in parts)
                except ValueError:
                    bounds = None
        try:
            value = float(self.value_ctrl.GetValue().strip() or "0")
        except ValueError:
            value = 0.0
        try:
            bands = max(1, int(self.bands_ctrl.GetValue().strip() or "1"))
        except ValueError:
            bands = 1
        return name, crs, width, height, bounds, value, bands


class RasterEditDialog(wx.Dialog):
    OP_FILL = 0
    OP_RECT = 1
    OP_ADD = 2
    OP_MUL = 3
    OP_INVERT = 4
    OP_GRAD = 5
    OP_NOISE = 6
    OP_SMOOTH = 7

    def __init__(self, parent, layer):
        super().__init__(parent, title=f"Edit Raster - {layer.name}", size=(520, 620))
        self.layer = layer
        self.parent_frame = parent
        self.rows = []
        self.controls = {}

        sizer = wx.BoxSizer(wx.VERTICAL)

        band_row = wx.BoxSizer(wx.HORIZONTAL)
        band_row.Add(wx.StaticText(self, label="Band:"), 0, wx.ALIGN_CENTER_VERTICAL)
        band_choices = self._band_choices()
        self.band_ctrl = wx.Choice(self, choices=band_choices)
        self.band_ctrl.SetSelection(self.layer.active_band - 1)
        self.band_ctrl.Enable(self.layer.band_count > 1)
        band_row.Add(self.band_ctrl, 0, wx.LEFT, 5)
        sizer.Add(band_row, 0, wx.ALL, 10)

        self.op_ctrl = wx.RadioBox(self, label="Operation", choices=[
            "Fill All", "Set Rectangle (Rows)", "Add Constant", "Multiply by",
            "Invert", "Add Gradient", "Add Noise", "Smooth"])
        sizer.Add(self.op_ctrl, 0, wx.ALL | wx.EXPAND, 10)

        self.panel = wx.Panel(self)
        self.params_sizer = wx.GridSizer(0, 2, 6, 8)
        self.panel.SetSizer(self.params_sizer)
        self._build_all_params()
        sizer.Add(self.panel, 0, wx.ALL | wx.EXPAND, 10)

        sizer.Add(wx.StaticText(self, label="Statistics (updated after each Apply):"),
                  0, wx.LEFT | wx.TOP, 10)
        self.stats_ctrl = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.stats_ctrl.InsertColumn(0, "Statistic", width=190)
        self.stats_ctrl.InsertColumn(1, "Value", width=260)
        sizer.Add(self.stats_ctrl, 1, wx.ALL | wx.EXPAND, 10)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        apply_btn = wx.Button(self, label="Apply")
        close_btn = wx.Button(self, label="Close")
        btn_row.Add(apply_btn)
        btn_row.Add(close_btn, 0, wx.LEFT, 5)
        sizer.Add(btn_row, 0, wx.ALL | wx.EXPAND, 10)

        self.SetSizer(sizer)
        self.op_ctrl.Bind(wx.EVT_RADIOBOX, self.on_op_change)
        self.band_ctrl.Bind(wx.EVT_CHOICE, self.on_band_change)
        apply_btn.Bind(wx.EVT_BUTTON, self.on_apply)
        close_btn.Bind(wx.EVT_BUTTON, lambda e: self.Close())
        self._update_params()
        self.refresh_stats()

    def _band_choices(self):
        choices = []
        for i in range(1, self.layer.band_count + 1):
            name = ""
            if self.layer.band_names and i <= len(self.layer.band_names):
                name = self.layer.band_names[i - 1]
            choices.append(f"{i}: {name}" if name else str(i))
        return choices

    def on_band_change(self, event):
        self.layer.set_active_band(self.band_ctrl.GetSelection() + 1)
        self.refresh_stats()

    def _add_text_param(self, op_index, name, label, default=""):
        st = wx.StaticText(self.panel, label=label)
        ctrl = wx.TextCtrl(self.panel, value=default)
        self.params_sizer.Add(st)
        self.params_sizer.Add(ctrl)
        self.rows.append((op_index, st, ctrl))
        self.controls[name] = ctrl

    def _add_choice_param(self, op_index, name, label, choices, default=0):
        st = wx.StaticText(self.panel, label=label)
        ctrl = wx.Choice(self.panel, choices=choices)
        ctrl.SetSelection(default)
        self.params_sizer.Add(st)
        self.params_sizer.Add(ctrl)
        self.rows.append((op_index, st, ctrl))
        self.controls[name] = ctrl

    def _build_all_params(self):
        self._add_text_param(self.OP_FILL, "fill_value", "Value")
        self._add_text_param(self.OP_RECT, "r0", "Row start", "0")
        self._add_text_param(self.OP_RECT, "r1", "Row end (exclusive)", "10")
        self._add_text_param(self.OP_RECT, "c0", "Col start", "0")
        self._add_text_param(self.OP_RECT, "c1", "Col end (exclusive)", "10")
        self._add_text_param(self.OP_RECT, "rect_value", "Value")
        self._add_text_param(self.OP_ADD, "add_value", "Value (can be negative)")
        self._add_text_param(self.OP_MUL, "mul_factor", "Factor")
        self._add_choice_param(self.OP_GRAD, "grad_dir", "Direction", [
            "North->South", "South->North", "West->East", "East->West", "NW->SE", "NE->SW"])
        self._add_text_param(self.OP_GRAD, "grad_start", "Start value", "0")
        self._add_text_param(self.OP_GRAD, "grad_end", "End value", "1000")
        self._add_text_param(self.OP_NOISE, "noise_amount", "Amount (std deviation)")
        self._add_text_param(self.OP_SMOOTH, "smooth_passes", "Passes (1-10)", "1")

    def on_op_change(self, event):
        self._update_params()

    def _update_params(self):
        op = self.op_ctrl.GetSelection()
        for op_index, st, ctrl in self.rows:
            show = (op_index == op)
            st.Show(show)
            ctrl.Show(show)
            self.params_sizer.Show(st, show)
            self.params_sizer.Show(ctrl, show)
        self.panel.Layout()
        self.Layout()

    def refresh_stats(self):
        self.stats_ctrl.DeleteAllItems()
        idx = 0
        for key, value in self.layer.stats.items():
            self.stats_ctrl.InsertItem(idx, key)
            self.stats_ctrl.SetItem(idx, 1, str(value))
            idx += 1
        self.stats_ctrl.InsertItem(idx, "Size")
        self.stats_ctrl.SetItem(idx, 1, f"{self.layer.height} x {self.layer.width} cells")
        idx += 1
        self.stats_ctrl.InsertItem(idx, "File")
        self.stats_ctrl.SetItem(idx, 1, self.layer.filepath or "Not saved yet")
        idx += 1

    def _num(self, name):
        ctrl = self.controls[name]
        if isinstance(ctrl, wx.Choice):
            return None
        text = ctrl.GetValue().strip()
        if not text:
            raise ValueError(f"'{name}' is empty")
        try:
            return float(text)
        except ValueError:
            raise ValueError(f"'{name}' must be a number")

    def _run_operation(self, op):
        if op == self.OP_FILL:
            self.layer.fill(self._num("fill_value"))
        elif op == self.OP_RECT:
            self.layer.fill_rect(self._num("r0"), self._num("r1"),
                                 self._num("c0"), self._num("c1"), self._num("rect_value"))
        elif op == self.OP_ADD:
            self.layer.add_constant(self._num("add_value"))
        elif op == self.OP_MUL:
            self.layer.multiply_constant(self._num("mul_factor"))
        elif op == self.OP_INVERT:
            self.layer.invert()
        elif op == self.OP_GRAD:
            direction = self.controls["grad_dir"].GetStringSelection()
            self.layer.add_gradient(direction, self._num("grad_start"), self._num("grad_end"))
        elif op == self.OP_NOISE:
            self.layer.add_noise(self._num("noise_amount"))
        elif op == self.OP_SMOOTH:
            self.layer.smooth(self._num("smooth_passes"))

    def on_apply(self, event):
        op = self.op_ctrl.GetSelection()
        snapshot = self.parent_frame.capture_snapshot()
        try:
            self._run_operation(op)
        except ValueError as e:
            wx.MessageBox(f"Invalid parameter: {e}", "Edit Raster", wx.OK | wx.ICON_ERROR)
            return
        self.parent_frame.push_snapshot(snapshot)
        self.refresh_stats()
        self.parent_frame.populate_terrain(self.layer)
        self.parent_frame._refresh_layer_node(self.layer)
        self.parent_frame.SetStatusText(f"Applied operation to '{self.layer.name}'")


class RasterPreviewDialog(wx.Dialog):
    def __init__(self, parent, layer):
        super().__init__(parent, title=f"Preview Raster - {layer.name}", size=(640, 620))
        self.layer = layer
        self.parent_frame = parent

        sizer = wx.BoxSizer(wx.VERTICAL)

        controls = wx.BoxSizer(wx.HORIZONTAL)
        controls.Add(wx.StaticText(self, label="Render:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.mode_ctrl = wx.Choice(self, choices=["Single band", "RGB composite"])
        self.mode_ctrl.SetSelection(0)
        controls.Add(self.mode_ctrl, 0, wx.LEFT, 5)
        controls.Add(wx.StaticText(self, label="Band:"), 0, wx.LEFT | wx.ALIGN_CENTER_VERTICAL, 15)
        band_choices = self._band_choices()
        self.band_ctrl = wx.Choice(self, choices=band_choices)
        self.band_ctrl.SetSelection(self.layer.active_band - 1)
        self.band_ctrl.Enable(self.layer.band_count > 1)
        controls.Add(self.band_ctrl, 0, wx.LEFT, 5)
        self.r_ctrl = wx.Choice(self, choices=band_choices)
        self.g_ctrl = wx.Choice(self, choices=band_choices)
        self.b_ctrl = wx.Choice(self, choices=band_choices)
        style = self.layer.style_with_defaults()
        rgb = style.get("rgb_bands") or [1, 2, 3]
        for ctrl, default in zip((self.r_ctrl, self.g_ctrl, self.b_ctrl), rgb):
            idx = max(1, min(int(default), self.layer.band_count)) - 1
            ctrl.SetSelection(idx)
        controls.Add(wx.StaticText(self, label="R:"), 0, wx.LEFT | wx.ALIGN_CENTER_VERTICAL, 15)
        controls.Add(self.r_ctrl, 0, wx.LEFT, 3)
        controls.Add(wx.StaticText(self, label="G:"), 0, wx.LEFT | wx.ALIGN_CENTER_VERTICAL, 8)
        controls.Add(self.g_ctrl, 0, wx.LEFT, 3)
        controls.Add(wx.StaticText(self, label="B:"), 0, wx.LEFT | wx.ALIGN_CENTER_VERTICAL, 8)
        controls.Add(self.b_ctrl, 0, wx.LEFT, 3)
        sizer.Add(controls, 0, wx.ALL, 10)

        import matplotlib
        matplotlib.use("WXAgg")
        from matplotlib.backends.backend_wxagg import FigureCanvasWxAgg
        from matplotlib.figure import Figure
        self.fig = Figure(figsize=(5.5, 4.5), dpi=100)
        self.canvas = FigureCanvasWxAgg(self, -1, self.fig)
        sizer.Add(self.canvas, 1, wx.ALL | wx.EXPAND, 10)

        self.stats_ctrl = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.stats_ctrl.InsertColumn(0, "Statistic", width=190)
        self.stats_ctrl.InsertColumn(1, "Value", width=280)
        sizer.Add(self.stats_ctrl, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 10)

        close_btn = wx.Button(self, label="Close")
        sizer.Add(close_btn, 0, wx.ALL | wx.EXPAND, 10)
        close_btn.Bind(wx.EVT_BUTTON, lambda e: self.Close())

        self.SetSizer(sizer)
        self.mode_ctrl.Bind(wx.EVT_CHOICE, self.on_mode_change)
        self.band_ctrl.Bind(wx.EVT_CHOICE, self.on_band_change)
        self.r_ctrl.Bind(wx.EVT_CHOICE, lambda e: self.render())
        self.g_ctrl.Bind(wx.EVT_CHOICE, lambda e: self.render())
        self.b_ctrl.Bind(wx.EVT_CHOICE, lambda e: self.render())
        self._update_mode_visibility()
        self.refresh_stats()
        self.render()

    def _band_choices(self):
        choices = []
        for i in range(1, self.layer.band_count + 1):
            name = ""
            if self.layer.band_names and i <= len(self.layer.band_names):
                name = self.layer.band_names[i - 1]
            choices.append(f"{i}: {name}" if name else str(i))
        return choices

    def _update_mode_visibility(self):
        rgb = self.mode_ctrl.GetSelection() == 1
        self.band_ctrl.Show(not rgb)
        for ctrl in (self.r_ctrl, self.g_ctrl, self.b_ctrl):
            ctrl.Show(rgb)
        self.Layout()

    def on_mode_change(self, event):
        self._update_mode_visibility()
        self.render()

    def render(self):
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        if self.mode_ctrl.GetSelection() == 1:
            rgb = self.layer.rgb_array(
                r_band=int(self.r_ctrl.GetSelection() + 1),
                g_band=int(self.g_ctrl.GetSelection() + 1),
                b_band=int(self.b_ctrl.GetSelection() + 1))
            arr = _decimate_render(rgb, max_dim=640)
            ax.imshow(arr, aspect="equal", interpolation="bilinear")
            ax.set_title("RGB composite")
        elif self.layer.data is not None:
            cmap = self.layer.style_with_defaults().get("colormap", "terrain")
            arr = _decimate_render(self.layer.data, max_dim=640)
            im = ax.imshow(arr, cmap=cmap, aspect="equal",
                           interpolation="bilinear")
            self.fig.colorbar(im, ax=ax)
            ax.set_title(f"Band {self.layer.active_band} of {self.layer.band_count}")
        ax.set_xlabel("Column")
        ax.set_ylabel("Row")
        self.canvas.draw()

    def on_band_change(self, event):
        self.layer.set_active_band(self.band_ctrl.GetSelection() + 1)
        self.refresh_stats()
        self.render()

    def refresh_stats(self):
        self.stats_ctrl.DeleteAllItems()
        idx = 0
        for key, value in self.layer.stats.items():
            self.stats_ctrl.InsertItem(idx, key)
            self.stats_ctrl.SetItem(idx, 1, str(value))
            idx += 1
        self.stats_ctrl.InsertItem(idx, "Active Band")
        self.stats_ctrl.SetItem(idx, 1, f"{self.layer.active_band} of {self.layer.band_count}")
        idx += 1
        self.stats_ctrl.InsertItem(idx, "Size")
        self.stats_ctrl.SetItem(idx, 1, f"{self.layer.height} x {self.layer.width} cells")
        idx += 1
        self.stats_ctrl.InsertItem(idx, "File")
        self.stats_ctrl.SetItem(idx, 1, self.layer.filepath or "Not saved yet")


class RasterStyleDialog(wx.Dialog):
    def __init__(self, parent, layer):
        super().__init__(parent, title=f"Raster Style - {layer.name}", size=(460, 460))
        self.layer = layer
        style = layer.style_with_defaults()

        import matplotlib
        matplotlib.use("WXAgg")
        from matplotlib.backends.backend_wxagg import FigureCanvasWxAgg
        from matplotlib.figure import Figure

        sizer = wx.BoxSizer(wx.VERTICAL)

        grid = wx.FlexGridSizer(cols=2, vgap=8, hgap=10)
        grid.AddGrowableCol(1)

        grid.Add(wx.StaticText(self, label="Colour map:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.cmap_ctrl = wx.Choice(self, choices=COLORMAP_CHOICES)
        cmap = style.get("colormap", "terrain")
        if cmap in COLORMAP_CHOICES:
            self.cmap_ctrl.SetStringSelection(cmap)
        else:
            self.cmap_ctrl.SetSelection(0)
        grid.Add(self.cmap_ctrl, 0, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="Render mode:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.mode_ctrl = wx.Choice(self, choices=["Single band (colour map)", "RGB composite (3 bands)"])
        self.mode_ctrl.SetSelection(1 if style.get("render_mode") == "rgb" else 0)
        grid.Add(self.mode_ctrl, 0, wx.EXPAND)

        band_choices = self._band_choices()
        rgb = style.get("rgb_bands") or [1, 2, 3]
        self.r_ctrl = wx.Choice(self, choices=band_choices)
        self.g_ctrl = wx.Choice(self, choices=band_choices)
        self.b_ctrl = wx.Choice(self, choices=band_choices)
        for ctrl, default in zip((self.r_ctrl, self.g_ctrl, self.b_ctrl), rgb):
            ctrl.SetSelection(max(1, min(int(default), self.layer.band_count)) - 1)
        grid.Add(wx.StaticText(self, label="Red band:"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self.r_ctrl, 0, wx.EXPAND)
        grid.Add(wx.StaticText(self, label="Green band:"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self.g_ctrl, 0, wx.EXPAND)
        grid.Add(wx.StaticText(self, label="Blue band:"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self.b_ctrl, 0, wx.EXPAND)

        sizer.Add(grid, 0, wx.ALL | wx.EXPAND, 10)

        self.preview_fig = Figure(figsize=(4.2, 3.0), dpi=90)
        self.preview_canvas = FigureCanvasWxAgg(self, -1, self.preview_fig)
        sizer.Add(self.preview_canvas, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 10)

        sizer.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.EXPAND, 10)

        self.SetSizer(sizer)

        self.cmap_ctrl.Bind(wx.EVT_CHOICE, lambda e: self._draw_preview())
        self.mode_ctrl.Bind(wx.EVT_CHOICE, self.on_mode_change)
        self.r_ctrl.Bind(wx.EVT_CHOICE, lambda e: self._draw_preview())
        self.g_ctrl.Bind(wx.EVT_CHOICE, lambda e: self._draw_preview())
        self.b_ctrl.Bind(wx.EVT_CHOICE, lambda e: self._draw_preview())
        self._update_mode_visibility()
        self._draw_preview()

    def _band_choices(self):
        choices = []
        for i in range(1, self.layer.band_count + 1):
            name = ""
            if self.layer.band_names and i <= len(self.layer.band_names):
                name = self.layer.band_names[i - 1]
            choices.append(f"{i}: {name}" if name else str(i))
        return choices

    def _update_mode_visibility(self):
        rgb = self.mode_ctrl.GetSelection() == 1
        self.cmap_ctrl.Enable(not rgb)
        for ctrl in (self.r_ctrl, self.g_ctrl, self.b_ctrl):
            ctrl.Enable(rgb)
        self.Layout()

    def on_mode_change(self, event):
        self._update_mode_visibility()
        self._draw_preview()

    def get_style(self):
        mode = "rgb" if self.mode_ctrl.GetSelection() == 1 else "single"
        return {
            "colormap": self.cmap_ctrl.GetStringSelection(),
            "render_mode": mode,
            "rgb_bands": [int(self.r_ctrl.GetSelection() + 1),
                          int(self.g_ctrl.GetSelection() + 1),
                          int(self.b_ctrl.GetSelection() + 1)],
        }

    def _draw_preview(self):
        cmap_name = self.cmap_ctrl.GetStringSelection()
        self.preview_fig.clear()
        ax = self.preview_fig.add_subplot(111)
        if self.mode_ctrl.GetSelection() == 1:
            rgb = self.layer.rgb_array(
                r_band=int(self.r_ctrl.GetSelection() + 1),
                g_band=int(self.g_ctrl.GetSelection() + 1),
                b_band=int(self.b_ctrl.GetSelection() + 1))
            arr = _decimate_render(rgb, max_dim=320)
            ax.imshow(arr, aspect="equal", interpolation="bilinear")
            ax.set_title("RGB composite")
        else:
            arr = _decimate_render(self.layer.data, max_dim=320)
            im = ax.imshow(arr, cmap=cmap_name, aspect="equal", interpolation="bilinear")
            self.preview_fig.colorbar(im, ax=ax)
            ax.set_title(f"Colour map: {cmap_name}")
        self.preview_canvas.draw()


class BandAddDialog(wx.Dialog):
    def __init__(self, parent, current_count):
        super().__init__(parent, title="Add Band(s)", size=(360, 180))
        sizer = wx.BoxSizer(wx.VERTICAL)
        grid = wx.FlexGridSizer(2, 2, 6, 8)
        grid.AddGrowableCol(1)
        grid.Add(wx.StaticText(self, label="Number of bands to add"))
        self.count_ctrl = wx.TextCtrl(self, value="1")
        grid.Add(self.count_ctrl)
        grid.Add(wx.StaticText(self, label="Current band count"))
        grid.Add(wx.StaticText(self, label=str(current_count)))
        sizer.Add(grid, 0, wx.ALL | wx.EXPAND, 10)
        sizer.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.EXPAND, 10)
        self.SetSizer(sizer)

    def get_count(self):
        try:
            return max(1, int(self.count_ctrl.GetValue().strip() or "1"))
        except ValueError:
            return 1


class BandManagerDialog(wx.Dialog):
    def __init__(self, parent, layer):
        super().__init__(parent, title=f"Manage Bands - {layer.name}", size=(600, 500))
        self.layer = layer
        self.parent_frame = parent
        self.changed = False

        sizer = wx.BoxSizer(wx.VERTICAL)

        self.list_ctrl = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.list_ctrl.InsertColumn(0, "Band", width=60)
        self.list_ctrl.InsertColumn(1, "Name", width=180)
        self.list_ctrl.InsertColumn(2, "Min", width=110)
        self.list_ctrl.InsertColumn(3, "Max", width=110)
        self.list_ctrl.InsertColumn(4, "Mean", width=110)
        self.list_ctrl.InsertColumn(5, "Active", width=60)
        sizer.Add(self.list_ctrl, 1, wx.ALL | wx.EXPAND, 10)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        set_active_btn = wx.Button(self, label="Set Active")
        add_btn = wx.Button(self, label="Add Band(s)...")
        dup_btn = wx.Button(self, label="Duplicate Band")
        rename_btn = wx.Button(self, label="Rename Band...")
        remove_btn = wx.Button(self, label="Remove Band")
        for b in (set_active_btn, add_btn, dup_btn, rename_btn, remove_btn):
            btn_row.Add(b, 0, wx.RIGHT, 5)
        sizer.Add(btn_row, 0, wx.LEFT | wx.RIGHT | wx.EXPAND, 10)

        sizer.Add(wx.StaticText(self,
                                label="Note: each band is independent. Editing operates on the active band."),
                  0, wx.LEFT | wx.RIGHT | wx.TOP, 10)

        close_btn = wx.Button(self, label="Close")
        sizer.Add(close_btn, 0, wx.ALL | wx.EXPAND, 10)

        self.SetSizer(sizer)

        set_active_btn.Bind(wx.EVT_BUTTON, self.on_set_active)
        add_btn.Bind(wx.EVT_BUTTON, self.on_add)
        dup_btn.Bind(wx.EVT_BUTTON, self.on_duplicate)
        rename_btn.Bind(wx.EVT_BUTTON, self.on_rename)
        remove_btn.Bind(wx.EVT_BUTTON, self.on_remove)
        close_btn.Bind(wx.EVT_BUTTON, lambda e: self.Close())
        self.list_ctrl.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_activate)
        self.refresh()
        if self.layer.band_count > 0:
            self.list_ctrl.Select(self.layer.active_band - 1)

    def refresh(self):
        sel = self.selected_band()
        self.list_ctrl.DeleteAllItems()
        for i in range(1, self.layer.band_count + 1):
            idx = self.list_ctrl.InsertItem(i - 1, str(i))
            name = ""
            if self.layer.band_names and i <= len(self.layer.band_names):
                name = self.layer.band_names[i - 1]
            self.list_ctrl.SetItem(idx, 1, name)
            stats = self.layer.band_stats(i)
            self.list_ctrl.SetItem(idx, 2, str(stats.get("Minimum Elevation", "")))
            self.list_ctrl.SetItem(idx, 3, str(stats.get("Maximum Elevation", "")))
            self.list_ctrl.SetItem(idx, 4, str(stats.get("Mean Elevation", "")))
            self.list_ctrl.SetItem(idx, 5, "Yes" if i == self.layer.active_band else "")
        if sel is not None and 1 <= sel <= self.layer.band_count:
            self.list_ctrl.Select(sel - 1)
        self.Layout()

    def selected_band(self):
        sel = self.list_ctrl.GetFirstSelected()
        if sel < 0:
            return None
        return sel + 1

    def on_activate(self, event):
        self.on_set_active(None)

    def _refresh_views(self):
        self.parent_frame.populate_terrain(self.layer)
        self.parent_frame._refresh_layer_node(self.layer)
        self.parent_frame.refresh_map()

    def on_set_active(self, event):
        band = self.selected_band()
        if band is None:
            wx.MessageBox("Select a band first.", "Manage Bands", wx.OK | wx.ICON_INFORMATION)
            return
        self.layer.set_active_band(band)
        self.changed = True
        self.refresh()
        self.parent_frame.populate_terrain(self.layer)
        self.parent_frame._refresh_layer_node(self.layer)
        self.parent_frame.SetStatusText(f"Active band set to {band} for '{self.layer.name}'")

    def on_add(self, event):
        dlg = BandAddDialog(self, self.layer.band_count)
        if dlg.ShowModal() == wx.ID_OK:
            for _ in range(dlg.get_count()):
                self.layer.add_band(value=0.0)
            self.changed = True
            self.refresh()
            self._refresh_views()
        dlg.Destroy()

    def on_duplicate(self, event):
        band = self.selected_band()
        if band is None:
            wx.MessageBox("Select a band first.", "Manage Bands", wx.OK | wx.ICON_INFORMATION)
            return
        self.layer.duplicate_band(band)
        self.changed = True
        self.refresh()
        self._refresh_views()

    def on_rename(self, event):
        band = self.selected_band()
        if band is None:
            wx.MessageBox("Select a band first.", "Manage Bands", wx.OK | wx.ICON_INFORMATION)
            return
        current = ""
        if self.layer.band_names and band <= len(self.layer.band_names):
            current = self.layer.band_names[band - 1]
        dlg = wx.TextEntryDialog(self, f"New name for band {band}:", "Rename Band", current)
        if dlg.ShowModal() == wx.ID_OK:
            self.layer.rename_band(band, dlg.GetValue())
            self.changed = True
            self.refresh()
            self._refresh_views()
        dlg.Destroy()

    def on_remove(self, event):
        band = self.selected_band()
        if band is None:
            wx.MessageBox("Select a band first.", "Manage Bands", wx.OK | wx.ICON_INFORMATION)
            return
        if self.layer.band_count <= 1:
            wx.MessageBox("At least one band is required.", "Manage Bands", wx.OK | wx.ICON_INFORMATION)
            return
        if wx.MessageBox(f"Remove band {band}?", "Manage Bands",
                         wx.YES_NO | wx.ICON_QUESTION) != wx.YES:
            return
        self.layer.remove_band(band)
        self.changed = True
        self.refresh()
        self._refresh_views()


class LayerStyleDialog(wx.Dialog):
    MODES = ["Single colour", "Graduated (colours)", "Categorised (categories)"]

    def __init__(self, parent, layer):
        super().__init__(parent, title=f"Layer Style - {layer.name}", size=(460, 540))
        self.layer = layer
        style = layer.style_with_defaults()

        import matplotlib
        matplotlib.use("WXAgg")
        from matplotlib.backends.backend_wxagg import FigureCanvasWxAgg
        from matplotlib.figure import Figure

        sizer = wx.BoxSizer(wx.VERTICAL)

        mode_row = wx.BoxSizer(wx.HORIZONTAL)
        mode_row.Add(wx.StaticText(self, label="Styling:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.mode_ctrl = wx.Choice(self, choices=self.MODES)
        mode_map = {"single": 0, "graduated": 1, "categorised": 2}
        self.mode_ctrl.SetSelection(mode_map.get(style.get("style_mode", "single"), 0))
        mode_row.Add(self.mode_ctrl, 1, wx.LEFT, 8)
        sizer.Add(mode_row, 0, wx.ALL | wx.EXPAND, 10)

        grid = wx.FlexGridSizer(cols=2, vgap=8, hgap=10)
        grid.AddGrowableCol(1)

        grid.Add(wx.StaticText(self, label="Attribute field:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.field_ctrl = wx.Choice(self, choices=[""] + sorted(layer.schema.keys()))
        field = style.get("attribute_field") or ""
        if field in layer.schema:
            self.field_ctrl.SetStringSelection(field)
        else:
            self.field_ctrl.SetSelection(0)
        grid.Add(self.field_ctrl, 0, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="Colour map:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.cmap_ctrl = wx.Choice(self, choices=COLORMAP_CHOICES)
        cmap = style.get("colormap", "viridis")
        if cmap in COLORMAP_CHOICES:
            self.cmap_ctrl.SetStringSelection(cmap)
        else:
            self.cmap_ctrl.SetSelection(0)
        grid.Add(self.cmap_ctrl, 0, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="Classes:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.classes_ctrl = wx.SpinCtrl(self, min=2, max=20,
                                        initial=int(style.get("class_count", 5)))
        grid.Add(self.classes_ctrl, 0, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="Colour:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.color_ctrl = wx.ColourPickerCtrl(self)
        self.color_ctrl.SetColour(wx.Colour(style["color"]))
        grid.Add(self.color_ctrl, 0, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="Line width:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.width_ctrl = wx.SpinCtrlDouble(self, min=0.0, max=20.0, inc=0.5,
                                            initial=float(style["line_width"]))
        grid.Add(self.width_ctrl, 0, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="Fill opacity (%):"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.alpha_ctrl = wx.SpinCtrlDouble(self, min=0.0, max=100.0, inc=5.0,
                                            initial=float(style["fill_alpha"]) * 100.0)
        grid.Add(self.alpha_ctrl, 0, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="Marker:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.marker_ctrl = wx.Choice(self, choices=MARKER_CHOICES)
        marker = style.get("marker", "circle")
        self.marker_ctrl.SetSelection(MARKER_CHOICES.index(marker) if marker in MARKER_CHOICES else 0)
        grid.Add(self.marker_ctrl, 0, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="Marker size:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.size_ctrl = wx.SpinCtrlDouble(self, min=1.0, max=30.0, inc=1.0,
                                           initial=float(style.get("marker_size", 6.0)))
        grid.Add(self.size_ctrl, 0, wx.EXPAND)

        sizer.Add(grid, 0, wx.LEFT | wx.RIGHT | wx.EXPAND, 10)

        self.preview_fig = Figure(figsize=(3.8, 2.4), dpi=90)
        self.preview_canvas = FigureCanvasWxAgg(self, -1, self.preview_fig)
        sizer.Add(self.preview_canvas, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 10)

        sizer.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.EXPAND, 10)

        self.SetSizer(sizer)

        self.mode_ctrl.Bind(wx.EVT_CHOICE, self._on_change)
        self.field_ctrl.Bind(wx.EVT_CHOICE, self._on_change)
        self.cmap_ctrl.Bind(wx.EVT_CHOICE, self._on_change)
        self.classes_ctrl.Bind(wx.EVT_SPINCTRL, self._on_change)
        self.color_ctrl.Bind(wx.EVT_COLOURPICKER_CHANGED, self._on_change)
        self.width_ctrl.Bind(wx.EVT_SPINCTRLDOUBLE, self._on_change)
        self.alpha_ctrl.Bind(wx.EVT_SPINCTRLDOUBLE, self._on_change)
        self.marker_ctrl.Bind(wx.EVT_CHOICE, self._on_change)
        self.size_ctrl.Bind(wx.EVT_SPINCTRLDOUBLE, self._on_change)
        self._update_enabled()
        self._draw_preview()

    def _on_change(self, event):
        self._update_enabled()
        self._draw_preview()

    def _update_enabled(self):
        mode = self.mode_ctrl.GetSelection()
        self.field_ctrl.Enable(mode in (1, 2))
        self.cmap_ctrl.Enable(mode in (1, 2))
        self.classes_ctrl.Enable(mode == 1)
        self.color_ctrl.Enable(mode == 0)

    def get_style(self):
        mode_map = {0: "single", 1: "graduated", 2: "categorised"}
        mode = mode_map[self.mode_ctrl.GetSelection()]
        return {
            "color": self.color_ctrl.GetColour().GetAsString(wx.C2S_HTML_SYNTAX),
            "line_width": round(float(self.width_ctrl.GetValue()), 2),
            "fill_alpha": round(float(self.alpha_ctrl.GetValue()) / 100.0, 2),
            "marker": MARKER_CHOICES[self.marker_ctrl.GetSelection()],
            "marker_size": round(float(self.size_ctrl.GetValue()), 2),
            "style_mode": mode,
            "attribute_field": self.field_ctrl.GetStringSelection() if mode != "single" else "",
            "colormap": self.cmap_ctrl.GetStringSelection(),
            "class_count": int(self.classes_ctrl.GetValue()),
        }

    def _draw_preview(self):
        style = self.get_style()
        self.preview_fig.clear()
        ax = self.preview_fig.add_subplot(111)
        if style["style_mode"] == "single":
            colour = wx.Colour(style["color"])
            rgb = (colour.Red() / 255.0, colour.Green() / 255.0, colour.Blue() / 255.0)
            marker = _MARKER_TO_MPL.get(style["marker"], "o")
            ax.plot([0, 1, 1, 0, 0], [0, 0, 1, 1, 0], color=rgb,
                    linewidth=style["line_width"], alpha=style["fill_alpha"],
                    solid_joinstyle="round")
            ax.scatter([0.5], [0.5], marker=marker, s=style["marker_size"] ** 2, color=rgb)
            ax.set_xticks([])
            ax.set_yticks([])
            ax.set_title("Preview")
        else:
            self._draw_attribute_preview(ax, style)
        ax.set_xlim(-0.15, 1.15)
        ax.set_ylim(-0.15, 1.15)
        ax.set_aspect("equal")
        self.preview_canvas.draw()

    def _draw_attribute_preview(self, ax, style):
        import matplotlib.patches as mpatches
        if style["style_mode"] == "graduated":
            n = style["class_count"]
            palette = _graduated_palette(style["colormap"], n)
            labels = [f"Class {i + 1}" for i in range(n)]
        else:
            field = style["attribute_field"]
            values = [f.properties.get(field) for f in self.layer.features]
            categories = []
            for v in values:
                if v is None:
                    continue
                key = str(v)
                if key not in categories:
                    categories.append(key)
            palette = _categorical_palette(len(categories))
            labels = categories or ["(no categories)"]
        for i, (swatch, label) in enumerate(zip(palette, labels)):
            x0 = -0.1
            y0 = 1.05 - (i + 1) * (1.1 / max(len(palette), 1))
            ax.add_patch(mpatches.Rectangle((x0, y0), 0.6, 0.85 * (1.1 / max(len(palette), 1)),
                                            facecolor=swatch, edgecolor="black", linewidth=0.5))
            ax.text(x0 + 0.7, y0 + 0.2, label, fontsize=8, va="center")
        ax.set_xlim(-0.15, 1.15)
        ax.set_ylim(-0.15, 1.15)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_title(f"Preview ({style['style_mode']})")


class MapCanvasPanel(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent)
        self.frame = parent

        import matplotlib
        matplotlib.use("WXAgg")
        from matplotlib.backends.backend_wxagg import FigureCanvasWxAgg, NavigationToolbar2WxAgg
        from matplotlib.figure import Figure

        self.fig = Figure(figsize=(5.0, 5.0), dpi=100)
        self.canvas = FigureCanvasWxAgg(self, -1, self.fig)
        self.toolbar = NavigationToolbar2WxAgg(self.canvas)

        canvas_sizer = wx.BoxSizer(wx.VERTICAL)
        canvas_sizer.Add(self.canvas, 1, wx.EXPAND)
        canvas_sizer.Add(self.toolbar, 0, wx.EXPAND)

        self.legend_panel = wx.Panel(self)
        self._legend_layers = []
        self.legend_list = wx.ListCtrl(self.legend_panel,
                                       style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.legend_list.InsertColumn(0, "Layer", width=150)
        self.legend_list.InsertColumn(1, "Visible", width=60)
        if hasattr(self.legend_list, "EnableCheckBoxes"):
            self.legend_list.EnableCheckBoxes(True)
        self.legend_list.Bind(wx.EVT_LIST_ITEM_SELECTED, self.on_legend_select)
        self.legend_list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_legend_item_activated)
        if hasattr(wx, "EVT_LIST_ITEM_CHECKED"):
            self.legend_list.Bind(wx.EVT_LIST_ITEM_CHECKED, self.on_legend_check)
        if hasattr(wx, "EVT_LIST_ITEM_UNCHECKED"):
            self.legend_list.Bind(wx.EVT_LIST_ITEM_UNCHECKED, self.on_legend_check)

        self.move_up_btn = wx.Button(self.legend_panel, label="Move Up")
        self.toggle_btn = wx.Button(self.legend_panel, label="Toggle Visibility")
        self.move_down_btn = wx.Button(self.legend_panel, label="Move Down")
        self.move_up_btn.Bind(wx.EVT_BUTTON, self.on_legend_move_up)
        self.toggle_btn.Bind(wx.EVT_BUTTON, self.on_legend_toggle)
        self.move_down_btn.Bind(wx.EVT_BUTTON, self.on_legend_move_down)

        legend_sizer = wx.BoxSizer(wx.VERTICAL)
        legend_sizer.Add(self.legend_list, 1, wx.EXPAND)
        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        btn_row.Add(self.move_up_btn, 1, wx.RIGHT, 4)
        btn_row.Add(self.toggle_btn, 1, wx.LEFT | wx.RIGHT, 4)
        btn_row.Add(self.move_down_btn, 1, wx.LEFT, 4)
        legend_sizer.Add(btn_row, 0, wx.ALL | wx.EXPAND, 6)
        self.legend_panel.SetSizer(legend_sizer)
        self.legend_panel.SetMinSize((230, -1))

        sizer = wx.BoxSizer(wx.HORIZONTAL)
        sizer.Add(self.legend_panel, 0, wx.EXPAND)
        sizer.Add(canvas_sizer, 1, wx.EXPAND)
        self.SetSizer(sizer)

        self.render_empty()

    def render_empty(self):
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        ax.set_title("Map Pane")
        ax.text(0.5, 0.5, "No visible layers to draw", ha="center", va="center",
                transform=ax.transAxes)
        ax.set_xticks([])
        ax.set_yticks([])
        self.canvas.draw()

    def render(self):
        if not self.IsShown():
            return
        visible_map_layers = [l for l in self.frame.all_layers()
                              if isinstance(l, MapLayer) and l.is_effectively_visible()]
        terrain_layers = [l for l in self.frame.all_layers()
                          if isinstance(l, TerrainLayer) and l.is_effectively_visible()]
        if not visible_map_layers and not terrain_layers:
            self.render_empty()
            return

        self.fig.clear()
        ax = self.fig.add_subplot(111)
        xmin = ymin = float("inf")
        xmax = ymax = float("-inf")

        def extend(xs, ys):
            nonlocal xmin, xmax, ymin, ymax
            if xs:
                xmin = min(xmin, min(xs))
                xmax = max(xmax, max(xs))
            if ys:
                ymin = min(ymin, min(ys))
                ymax = max(ymax, max(ys))

        for terrain in terrain_layers:
            if terrain.data is not None and terrain.bounds is not None:
                style = terrain.style_with_defaults()
                extent = (terrain.bounds.left, terrain.bounds.right,
                          terrain.bounds.top, terrain.bounds.bottom)
                if style.get("render_mode") == "rgb":
                    rgb = terrain.rgb_array(*style.get("rgb_bands") or [])
                    if rgb is not None:
                        arr = _decimate_render(rgb)
                        ax.imshow(arr, extent=extent, origin="upper",
                                  interpolation="bilinear")
                else:
                    cmap = style.get("colormap", "terrain")
                    arr = _decimate_render(terrain.data)
                    ax.imshow(arr, cmap=cmap,
                              extent=extent,
                              origin="upper", interpolation="bilinear")
                extend([terrain.bounds.left, terrain.bounds.right],
                       [terrain.bounds.bottom, terrain.bounds.top])

        for layer in visible_map_layers:
            style = layer.style_with_defaults()
            base_color = style["color"]
            line_width = float(style["line_width"])
            fill_alpha = float(style["fill_alpha"])
            marker = _MARKER_TO_MPL.get(style["marker"], "o")
            marker_size = float(style["marker_size"])
            colours = _feature_colour_map(layer, style)
            for feature in layer.features:
                color = base_color
                if colours:
                    color = colours.get(feature) or base_color
                extend(*self._draw_geometry(ax, feature.geometry, color, line_width,
                                            fill_alpha, marker, marker_size))

        ax.set_title("Map Pane")
        if xmin == float("inf"):
            ax.text(0.5, 0.5, "No visible geometry to draw", ha="center", va="center",
                    transform=ax.transAxes)
            ax.set_xticks([])
            ax.set_yticks([])
        else:
            span = max(xmax - xmin, ymax - ymin, 1e-6)
            margin = 0.05 * span
            ax.set_xlim(xmin - margin, xmax + margin)
            ax.set_ylim(ymin - margin, ymax + margin)
            if self._is_geographic():
                mean_lat = (ymin + ymax) / 2.0
                aspect = 1.0 / math.cos(math.radians(mean_lat)) if abs(mean_lat) < 89.0 else 1.0
                ax.set_aspect(aspect)
            else:
                ax.set_aspect("equal")
            ax.grid(True, linestyle=":", alpha=0.5)
        self.canvas.draw()

    def _is_geographic(self):
        crss = [l.crs for l in self.frame.all_layers() if getattr(l, "crs", None)]
        if not crss:
            return True
        low = " ".join(str(c).lower() for c in crss)
        if any(token in low for token in ("3857", "utm", "projcs", "3263", "54030")):
            return False
        return True

    def update_legend(self):
        if not self.IsShown():
            return
        self.legend_list.DeleteAllItems()
        self._legend_layers = []
        index = 0
        for layer in self.frame.all_layers():
            self.legend_list.InsertItem(index, layer.name)
            if layer.is_effectively_visible():
                self.legend_list.SetItemTextColour(index, wx.Colour(_legend_colour(layer)))
            else:
                self.legend_list.SetItemTextColour(index, wx.Colour(150, 150, 150))
            self.legend_list.SetItem(index, 1, "Yes" if layer.visible else "No")
            self._legend_layers.append(layer)
            if hasattr(self.legend_list, "CheckItem"):
                self.legend_list.CheckItem(index, bool(layer.visible))
            index += 1

    def select_layer(self, layer):
        if not isinstance(layer, Layer):
            return
        for i, legend_layer in enumerate(self._legend_layers):
            if legend_layer is layer:
                self.legend_list.SetItemState(
                    i, wx.LIST_STATE_SELECTED | wx.LIST_STATE_FOCUSED,
                    wx.LIST_STATE_SELECTED | wx.LIST_STATE_FOCUSED)
                self.legend_list.EnsureVisible(i)
                return

    def _selected_legend_layer(self):
        index = self.legend_list.GetFirstSelected()
        if 0 <= index < len(self._legend_layers):
            return self._legend_layers[index]
        return None

    def on_legend_select(self, event):
        index = event.GetIndex()
        if not (0 <= index < len(self._legend_layers)):
            return
        layer = self._legend_layers[index]
        if isinstance(layer, Layer) and not self.frame._updating_selection:
            self.frame._updating_selection = True
            try:
                node = self.frame._find_layer_node(layer)
                if node.IsOk():
                    self.frame.tree.SelectItem(node)
            finally:
                self.frame._updating_selection = False

    def on_legend_item_activated(self, event):
        index = event.GetIndex()
        if not (0 <= index < len(self._legend_layers)):
            return
        layer = self._legend_layers[index]
        if isinstance(layer, Layer):
            self.frame._toggle_visibility(layer)

    def on_legend_toggle(self, event):
        layer = self._selected_legend_layer()
        if isinstance(layer, Layer):
            self.frame._toggle_visibility(layer)

    def on_legend_check(self, event):
        index = event.GetIndex()
        if not (0 <= index < len(self._legend_layers)):
            return
        layer = self._legend_layers[index]
        if not isinstance(layer, Layer):
            return
        if hasattr(self.legend_list, "IsItemChecked"):
            desired = bool(self.legend_list.IsItemChecked(index))
        else:
            desired = not layer.visible
        if layer.visible != desired:
            self.frame._toggle_visibility(layer)

    def on_legend_move_up(self, event):
        layer = self._selected_legend_layer()
        if isinstance(layer, Layer) and self.frame._move_layer(layer, -1):
            self.select_layer(layer)

    def on_legend_move_down(self, event):
        layer = self._selected_legend_layer()
        if isinstance(layer, Layer) and self.frame._move_layer(layer, 1):
            self.select_layer(layer)

    def _draw_geometry(self, ax, geometry, color, line_width, fill_alpha, marker, marker_size):
        if not isinstance(geometry, dict):
            return [], []
        gtype = geometry.get("type")
        coords = geometry.get("coordinates", [])
        xs, ys = [], []

        def line_points(part):
            return [(p[0], p[1]) for p in part]

        if gtype == "Point":
            xs, ys = [coords[0]], [coords[1]]
            ax.plot(xs, ys, marker=marker, color=color, markersize=marker_size)
        elif gtype == "MultiPoint":
            for pt in coords:
                xs.append(pt[0])
                ys.append(pt[1])
            ax.plot(xs, ys, marker=marker, color=color, linestyle="None",
                    markersize=marker_size)
        elif gtype == "LineString":
            xs, ys = [p[0] for p in coords], [p[1] for p in coords]
            ax.plot(xs, ys, color=color, linewidth=line_width)
        elif gtype == "MultiLineString":
            for part in coords:
                pts = line_points(part)
                ax.plot([p[0] for p in pts], [p[1] for p in pts],
                        color=color, linewidth=line_width)
                xs.extend([p[0] for p in pts])
                ys.extend([p[1] for p in pts])
        elif gtype == "Polygon":
            for ring in coords:
                pts = line_points(ring)
                ax.fill([p[0] for p in pts], [p[1] for p in pts], facecolor=color,
                        edgecolor=color, alpha=fill_alpha, linewidth=line_width)
                xs.extend([p[0] for p in pts])
                ys.extend([p[1] for p in pts])
        elif gtype == "MultiPolygon":
            for poly in coords:
                for ring in poly:
                    pts = line_points(ring)
                    ax.fill([p[0] for p in pts], [p[1] for p in pts], facecolor=color,
                            edgecolor=color, alpha=fill_alpha, linewidth=line_width)
                    xs.extend([p[0] for p in pts])
                    ys.extend([p[1] for p in pts])
        elif gtype == "GeometryCollection":
            for sub in geometry.get("geometries", []):
                sub_xs, sub_ys = self._draw_geometry(ax, sub, color, line_width,
                                                     fill_alpha, marker, marker_size)
                xs.extend(sub_xs)
                ys.extend(sub_ys)
        return xs, ys


class AddFeatureDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="Add Feature", size=(520, 540),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.points = []

        sizer = wx.BoxSizer(wx.VERTICAL)
        grid = wx.GridSizer(2, 2, 5, 5)

        grid.Add(wx.StaticText(self, label="Name"))
        self.name_ctrl = wx.TextCtrl(self)
        grid.Add(self.name_ctrl)

        grid.Add(wx.StaticText(self, label="Geometry Type"))
        self.type_ctrl = wx.Choice(self, choices=["Point", "LineString", "Polygon", "MultiPoint"])
        self.type_ctrl.SetSelection(0)
        grid.Add(self.type_ctrl)

        sizer.Add(grid, 0, wx.ALL | wx.EXPAND, 10)

        hint = wx.StaticText(self, label="Add each point with its X and Y values, then press Add Point. "
                                         "X = east/west, Y = north/south. "
                                         "The points are collected into GeoJSON automatically.")
        sizer.Add(hint, 0, wx.LEFT | wx.RIGHT, 10)

        point_row = wx.BoxSizer(wx.HORIZONTAL)
        point_row.Add(wx.StaticText(self, label="Point X:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.x_ctrl = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)
        point_row.Add(self.x_ctrl, 1, wx.LEFT, 5)
        point_row.Add(wx.StaticText(self, label="Y:"), 0, wx.LEFT | wx.ALIGN_CENTER_VERTICAL, 10)
        self.y_ctrl = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)
        point_row.Add(self.y_ctrl, 1, wx.LEFT, 5)
        add_btn = wx.Button(self, label="Add Point")
        point_row.Add(add_btn, 0, wx.LEFT, 5)
        sizer.Add(point_row, 0, wx.ALL | wx.EXPAND, 10)

        sizer.Add(wx.StaticText(self, label="Points (collected into GeoJSON):"), 0, wx.LEFT, 10)
        self.points_ctrl = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.LC_HRULES | wx.LC_VRULES)
        self.points_ctrl.InsertColumn(0, "#", width=40)
        self.points_ctrl.InsertColumn(1, "X", width=180)
        self.points_ctrl.InsertColumn(2, "Y", width=180)
        sizer.Add(self.points_ctrl, 1, wx.ALL | wx.EXPAND, 10)

        list_btn_row = wx.BoxSizer(wx.HORIZONTAL)
        paste_btn = wx.Button(self, label="Paste Points...")
        remove_btn = wx.Button(self, label="Remove Point")
        clear_btn = wx.Button(self, label="Clear")
        list_btn_row.Add(paste_btn)
        list_btn_row.Add(remove_btn, 0, wx.LEFT, 5)
        list_btn_row.Add(clear_btn, 0, wx.LEFT, 5)
        sizer.Add(list_btn_row, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        sizer.Add(wx.StaticText(self, label="Properties (JSON, optional):"), 0, wx.LEFT, 10)
        self.props_ctrl = wx.TextCtrl(self, style=wx.TE_MULTILINE)
        self.props_ctrl.SetMinSize((-1, 60))
        sizer.Add(self.props_ctrl, 0, wx.ALL | wx.EXPAND, 10)

        btn_sizer = wx.StdDialogButtonSizer()
        ok_btn = wx.Button(self, wx.ID_OK)
        cancel_btn = wx.Button(self, wx.ID_CANCEL)
        btn_sizer.AddButton(ok_btn)
        btn_sizer.AddButton(cancel_btn)
        btn_sizer.Realize()
        sizer.Add(btn_sizer, 0, wx.ALL | wx.EXPAND, 10)

        self.SetSizer(sizer)
        self.SetMinSize((460, 420))

        add_btn.Bind(wx.EVT_BUTTON, self.on_add_point)
        paste_btn.Bind(wx.EVT_BUTTON, self.on_paste)
        remove_btn.Bind(wx.EVT_BUTTON, self.on_remove_point)
        clear_btn.Bind(wx.EVT_BUTTON, self.on_clear)
        self.x_ctrl.Bind(wx.EVT_TEXT_ENTER, self.on_add_point)
        self.y_ctrl.Bind(wx.EVT_TEXT_ENTER, self.on_add_point)

        self.x_ctrl.SetFocus()

    def on_add_point(self, event):
        x_text = self.x_ctrl.GetValue().strip()
        y_text = self.y_ctrl.GetValue().strip()
        if not x_text or not y_text:
            wx.MessageBox("Enter both X and Y values.", "Add Point", wx.OK | wx.ICON_INFORMATION)
            return
        try:
            x = float(x_text)
            y = float(y_text)
        except ValueError:
            wx.MessageBox("X and Y must be numbers.", "Add Point", wx.OK | wx.ICON_ERROR)
            return
        self.points.append([x, y])
        idx = self.points_ctrl.GetItemCount()
        self.points_ctrl.InsertItem(idx, str(idx + 1))
        self.points_ctrl.SetItem(idx, 1, str(x))
        self.points_ctrl.SetItem(idx, 2, str(y))
        self.points_ctrl.EnsureVisible(idx)
        self.x_ctrl.SetValue("")
        self.y_ctrl.SetValue("")
        self.x_ctrl.SetFocus()

    def on_remove_point(self, event):
        idx = self.points_ctrl.GetFirstSelected()
        if idx < 0:
            wx.MessageBox("Select a point first.", "Remove Point", wx.OK | wx.ICON_INFORMATION)
            return
        self.points.pop(idx)
        self.points_ctrl.DeleteItem(idx)
        self._renumber()

    def on_clear(self, event):
        self.points = []
        self.points_ctrl.DeleteAllItems()
        self.x_ctrl.SetFocus()

    def _renumber(self):
        for i in range(self.points_ctrl.GetItemCount()):
            self.points_ctrl.SetItem(i, 0, str(i + 1))

    def on_paste(self, event):
        dlg = wx.Dialog(self, title="Paste Points", size=(420, 260))
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(dlg, label="One 'x, y' per line, or paste raw GeoJSON coordinates:"), 0, wx.ALL, 10)
        text = wx.TextCtrl(dlg, style=wx.TE_MULTILINE)
        sizer.Add(text, 1, wx.ALL | wx.EXPAND, 10)
        bs = wx.StdDialogButtonSizer()
        ok = wx.Button(dlg, wx.ID_OK)
        cancel = wx.Button(dlg, wx.ID_CANCEL)
        bs.AddButton(ok)
        bs.AddButton(cancel)
        bs.Realize()
        sizer.Add(bs, 0, wx.ALL | wx.EXPAND, 10)
        dlg.SetSizer(sizer)
        if dlg.ShowModal() == wx.ID_OK:
            try:
                rows = self._parse_points_text(text.GetValue())
            except ValueError as e:
                wx.MessageBox(str(e), "Paste Points", wx.OK | wx.ICON_ERROR)
                dlg.Destroy()
                return
            for p in rows:
                self.points.append(p)
                idx = self.points_ctrl.GetItemCount()
                self.points_ctrl.InsertItem(idx, str(idx + 1))
                self.points_ctrl.SetItem(idx, 1, str(p[0]))
                self.points_ctrl.SetItem(idx, 2, str(p[1]))
            self.points_ctrl.EnsureVisible(self.points_ctrl.GetItemCount() - 1)
        dlg.Destroy()

    def _parse_points_text(self, text):
        text = text.strip()
        if not text:
            return []
        if text.lstrip().startswith("["):
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                raise ValueError("Invalid GeoJSON coordinates JSON.")
            return self._flatten_points(data)
        rows = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 2:
                raise ValueError(f"Invalid coordinate line: '{line}'")
            try:
                rows.append([float(parts[0]), float(parts[1])])
            except ValueError:
                raise ValueError(f"Invalid numbers in line: '{line}'")
        return rows

    def _flatten_points(self, data):
        points = []

        def walk(node):
            if not isinstance(node, list):
                return
            if len(node) >= 2 and all(isinstance(v, (int, float)) for v in node[:2]):
                points.append([float(node[0]), float(node[1])])
                return
            for child in node:
                walk(child)

        walk(data)
        if not points:
            raise ValueError("No coordinate pairs found in pasted data.")
        return points

    def _build_coordinates(self, geom_type):
        points = self.points
        if not points:
            raise ValueError("Add at least one point using the X and Y fields (Add Point).")
        if geom_type == "Point":
            if len(points) != 1:
                raise ValueError("A Point needs exactly one point. Remove extra points.")
            return points[0]
        if geom_type == "LineString":
            if len(points) < 2:
                raise ValueError("A LineString needs at least 2 points.")
            return points
        if geom_type == "Polygon":
            if len(points) < 3:
                raise ValueError("A Polygon needs at least 3 points.")
            ring = [list(p) for p in points]
            if ring[0] != ring[-1]:
                ring.append(list(ring[0]))
            return [ring]
        if geom_type == "MultiPoint":
            return points
        raise ValueError(f"Unsupported geometry type: {geom_type}")

    def get_data(self):
        name = self.name_ctrl.GetValue().strip()
        if not name:
            raise ValueError("Enter a feature name.")
        geom_type = self.type_ctrl.GetStringSelection()
        coordinates = self._build_coordinates(geom_type)
        props = {}
        prop_text = self.props_ctrl.GetValue().strip()
        if prop_text:
            try:
                props = json.loads(prop_text)
            except json.JSONDecodeError:
                raise ValueError("Properties must be valid JSON.")
            if not isinstance(props, dict):
                raise ValueError("Properties must be a JSON object.")
        return name, geom_type, coordinates, props


class GeometryEditDialog(wx.Dialog):
    def __init__(self, parent, geometry):
        super().__init__(parent, title="Edit Geometry (GeoJSON)", size=(520, 420))

        sizer = wx.BoxSizer(wx.VERTICAL)

        label = wx.StaticText(self, label="Paste the geometry as GeoJSON JSON:")
        sizer.Add(label, 0, wx.ALL | wx.EXPAND, 10)

        self.text_ctrl = wx.TextCtrl(self, style=wx.TE_MULTILINE)
        self.text_ctrl.SetValue(json.dumps(geometry, indent=2))
        sizer.Add(self.text_ctrl, 1, wx.ALL | wx.EXPAND, 10)

        btn_sizer = wx.StdDialogButtonSizer()
        ok_btn = wx.Button(self, wx.ID_OK)
        cancel_btn = wx.Button(self, wx.ID_CANCEL)
        btn_sizer.AddButton(ok_btn)
        btn_sizer.AddButton(cancel_btn)
        btn_sizer.Realize()
        sizer.Add(btn_sizer, 0, wx.ALL | wx.EXPAND, 10)

        self.SetSizer(sizer)

    def get_geometry(self):
        return json.loads(self.text_ctrl.GetValue())


class LayerChoiceDialog(wx.Dialog):
    def __init__(self, parent, title, layers, exclude=None):
        super().__init__(parent, title=title, size=(380, 160))

        self.layer_map = [l for l in layers if l is not exclude and isinstance(l, MapLayer)]

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label="Target layer:"), 0, wx.ALL, 10)
        self.choice = wx.Choice(self, choices=[l.name for l in self.layer_map])
        if self.layer_map:
            self.choice.SetSelection(0)
        sizer.Add(self.choice, 0, wx.ALL | wx.EXPAND, 10)

        btn_sizer = wx.StdDialogButtonSizer()
        ok_btn = wx.Button(self, wx.ID_OK)
        cancel_btn = wx.Button(self, wx.ID_CANCEL)
        btn_sizer.AddButton(ok_btn)
        btn_sizer.AddButton(cancel_btn)
        btn_sizer.Realize()
        sizer.Add(btn_sizer, 0, wx.ALL | wx.EXPAND, 10)

        self.SetSizer(sizer)

    def get_selected(self):
        sel = self.choice.GetSelection()
        if sel >= 0 and sel < len(self.layer_map):
            return self.layer_map[sel]
        return None


class SplitCoordinatesDialog(wx.Dialog):
    def __init__(self, parent, layer):
        super().__init__(parent, title="Split Coordinates into X/Y Fields", size=(440, 240))

        sizer = wx.BoxSizer(wx.VERTICAL)
        grid = wx.GridSizer(3, 2, 5, 5)

        grid.Add(wx.StaticText(self, label="Source"))
        self.sources = ["__geometry__"]
        self.source_ctrl = wx.Choice(self, choices=["Geometry (centroid)"])
        for key in layer.coordinate_properties():
            self.sources.append(key)
            self.source_ctrl.Append(key)
        self.source_ctrl.SetSelection(0)
        grid.Add(self.source_ctrl)

        grid.Add(wx.StaticText(self, label="X Field"))
        self.x_ctrl = wx.TextCtrl(self, value="x")
        grid.Add(self.x_ctrl)

        grid.Add(wx.StaticText(self, label="Y Field"))
        self.y_ctrl = wx.TextCtrl(self, value="y")
        grid.Add(self.y_ctrl)

        sizer.Add(grid, 0, wx.ALL | wx.EXPAND, 10)

        btn_sizer = wx.StdDialogButtonSizer()
        ok_btn = wx.Button(self, wx.ID_OK)
        cancel_btn = wx.Button(self, wx.ID_CANCEL)
        btn_sizer.AddButton(ok_btn)
        btn_sizer.AddButton(cancel_btn)
        btn_sizer.Realize()
        sizer.Add(btn_sizer, 0, wx.ALL | wx.EXPAND, 10)

        self.SetSizer(sizer)

    def get_data(self):
        source = self.sources[self.source_ctrl.GetSelection()]
        x_field = self.x_ctrl.GetValue().strip() or "x"
        y_field = self.y_ctrl.GetValue().strip() or "y"
        return source, x_field, y_field


class SuitabilityAnalysisDialog(wx.Dialog):
    def __init__(self, parent, map_layers, terrain_layers):
        super().__init__(parent, title="Resource Suitability Analysis", size=(480, 460))

        self.deposit_map = [l for l in map_layers if l.features]
        self.corridor_map = list(map_layers)
        self.terrain_map = list(terrain_layers)

        sizer = wx.BoxSizer(wx.VERTICAL)
        grid = wx.FlexGridSizer(cols=2, vgap=8, hgap=10)
        grid.AddGrowableCol(1)

        grid.Add(wx.StaticText(self, label="&Deposit Points Layer:"),
                 0, wx.ALIGN_CENTER_VERTICAL)
        self.deposits_ctrl = wx.Choice(self, choices=[l.name for l in self.deposit_map])
        if self.deposit_map:
            self.deposits_ctrl.SetSelection(0)
        grid.Add(self.deposits_ctrl, 0, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="&Transport Corridors Layer:"),
                 0, wx.ALIGN_CENTER_VERTICAL)
        self.corridors_ctrl = wx.Choice(self,
                                        choices=["None"] + [l.name for l in self.corridor_map])
        self.corridors_ctrl.SetSelection(0)
        grid.Add(self.corridors_ctrl, 0, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="&Terrain Layer (optional):"),
                 0, wx.ALIGN_CENTER_VERTICAL)
        self.terrain_ctrl = wx.Choice(self,
                                      choices=["None"] + [l.name for l in self.terrain_map])
        self.terrain_ctrl.SetSelection(0)
        grid.Add(self.terrain_ctrl, 0, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="&Cost Surface Layer (optional):"),
                 0, wx.ALIGN_CENTER_VERTICAL)
        self.cost_ctrl = wx.Choice(self,
                                   choices=["None"] + [l.name for l in self.terrain_map])
        self.cost_ctrl.SetSelection(0)
        grid.Add(self.cost_ctrl, 0, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="Cost &Band:"),
                 0, wx.ALIGN_CENTER_VERTICAL)
        self.band_ctrl = wx.Choice(self, choices=["Band 1"])
        grid.Add(self.band_ctrl, 0, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="Network reach weight (0-1):"),
                 0, wx.ALIGN_CENTER_VERTICAL)
        self.weight_ctrl = wx.TextCtrl(self, value="0.0")
        grid.Add(self.weight_ctrl, 0, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="Max distance (km):"),
                 0, wx.ALIGN_CENTER_VERTICAL)
        self.maxdist_ctrl = wx.TextCtrl(self, value="50")
        grid.Add(self.maxdist_ctrl, 0, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="Max slope % (blank = no limit):"),
                 0, wx.ALIGN_CENTER_VERTICAL)
        self.slope_ctrl = wx.TextCtrl(self, value="")
        grid.Add(self.slope_ctrl, 0, wx.EXPAND)

        sizer.Add(grid, 0, wx.ALL | wx.EXPAND, 10)
        sizer.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.EXPAND, 10)

        self.SetSizer(sizer)
        self._populate_band_ctrl()
        self.cost_ctrl.Bind(wx.EVT_CHOICE, lambda e: self._populate_band_ctrl())

    def _populate_band_ctrl(self):
        names = []
        active = 1
        idx = self.cost_ctrl.GetSelection() - 1
        if 0 <= idx < len(self.terrain_map):
            layer = self.terrain_map[idx]
            names = (list(layer.band_names) if layer.band_names
                     else ["Band %d" % (i + 1) for i in range(layer.band_count)])
            active = layer.active_band
        self.band_ctrl.Clear()
        if names:
            self.band_ctrl.AppendItems(names)
            self.band_ctrl.SetSelection(max(0, min(active - 1, len(names) - 1)))

    def get_data(self):
        deposits = (self.deposit_map[self.deposits_ctrl.GetSelection()]
                    if self.deposit_map else None)
        corridor_idx = self.corridors_ctrl.GetSelection() - 1
        corridors = self.corridor_map[corridor_idx] if corridor_idx >= 0 else None
        terrain_idx = self.terrain_ctrl.GetSelection() - 1
        terrain = self.terrain_map[terrain_idx] if terrain_idx >= 0 else None
        cost_idx = self.cost_ctrl.GetSelection() - 1
        cost_layer = self.terrain_map[cost_idx] if cost_idx >= 0 else None
        band = self.band_ctrl.GetSelection() + 1 if self.band_ctrl.GetCount() else 1
        weight_text = self.weight_ctrl.GetValue().strip()
        try:
            cost_weight = float(weight_text) if weight_text else 0.0
        except ValueError:
            cost_weight = 0.0
        try:
            max_km = float(self.maxdist_ctrl.GetValue())
        except ValueError:
            max_km = 50.0
        slope_text = self.slope_ctrl.GetValue().strip()
        try:
            max_slope = float(slope_text) if slope_text else None
        except ValueError:
            max_slope = None
        return (deposits, corridors, terrain, max_km, max_slope,
                cost_layer, band, cost_weight)


class OrgReachDialog(wx.Dialog):
    METHODS = ["Buffers (multi-ring)", "Voronoi (Thiessen cells)",
               "Density (heat grid)"]

    def __init__(self, parent, map_layers):
        super().__init__(parent, title="Org Network Reach", size=(460, 360))

        self.org_map = [l for l in map_layers if l.features]
        self.corridor_map = list(map_layers)

        sizer = wx.BoxSizer(wx.VERTICAL)
        grid = wx.FlexGridSizer(cols=2, vgap=8, hgap=10)
        grid.AddGrowableCol(1)

        grid.Add(wx.StaticText(self, label="&Org Points Layer:"),
                 0, wx.ALIGN_CENTER_VERTICAL)
        self.org_ctrl = wx.Choice(self, choices=[l.name for l in self.org_map])
        if self.org_map:
            self.org_ctrl.SetSelection(0)
        grid.Add(self.org_ctrl, 0, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="&Analysis method:"),
                 0, wx.ALIGN_CENTER_VERTICAL)
        self.method_ctrl = wx.Choice(self, choices=self.METHODS)
        self.method_ctrl.SetSelection(0)
        grid.Add(self.method_ctrl, 0, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="Buffer rings (km, comma-separated):"),
                 0, wx.ALIGN_CENTER_VERTICAL)
        self.rings_ctrl = wx.TextCtrl(self, value="10, 50, 100")
        grid.Add(self.rings_ctrl, 0, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="Transport corridors layer:"),
                 0, wx.ALIGN_CENTER_VERTICAL)
        self.corridors_ctrl = wx.Choice(self,
                                        choices=["None"] + [l.name for l in self.corridor_map])
        self.corridors_ctrl.SetSelection(0)
        grid.Add(self.corridors_ctrl, 0, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="Risk radius (km):"),
                 0, wx.ALIGN_CENTER_VERTICAL)
        self.risk_ctrl = wx.TextCtrl(self, value="25")
        grid.Add(self.risk_ctrl, 0, wx.EXPAND)

        sizer.Add(grid, 0, wx.ALL | wx.EXPAND, 10)
        sizer.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.EXPAND, 10)

        self.SetSizer(sizer)

    def get_data(self):
        org = (self.org_map[self.org_ctrl.GetSelection()] if self.org_map else None)
        method = {0: "buffers", 1: "voronoi", 2: "density"}[self.method_ctrl.GetSelection()]
        rings = tuple(r for part in self.rings_ctrl.GetValue().split(",")
                      for r in [part.strip()]
                      if r)
        try:
            rings = tuple(float(r) for r in rings) or (10.0, 50.0, 100.0)
        except ValueError:
            rings = (10.0, 50.0, 100.0)
        corridor_idx = self.corridors_ctrl.GetSelection() - 1
        corridors = self.corridor_map[corridor_idx] if corridor_idx >= 0 else None
        try:
            risk_km = float(self.risk_ctrl.GetValue())
        except ValueError:
            risk_km = 25.0
        return org, method, rings, corridors, risk_km


class NetworkCostDialog(wx.Dialog):
    METHODS = ["Least-cost path (pair)", "Chain (waypoints in order)",
               "Star (hub = first point)", "Tree (minimum spanning network)"]

    def __init__(self, parent, map_layers, terrain_layers):
        super().__init__(parent, title="Network Cost Surfaces", size=(500, 400))

        self.point_map = [l for l in map_layers if l.features]
        self.terrain_map = list(terrain_layers)

        self._sizer = wx.BoxSizer(wx.VERTICAL)
        grid = wx.FlexGridSizer(cols=2, vgap=8, hgap=10)
        grid.AddGrowableCol(1)

        grid.Add(wx.StaticText(self, label="&Points Layer:"),
                 0, wx.ALIGN_CENTER_VERTICAL)
        self.points_ctrl = wx.Choice(self, choices=[l.name for l in self.point_map])
        if self.point_map:
            self.points_ctrl.SetSelection(0)
        grid.Add(self.points_ctrl, 0, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="&Cost Surface Layer:"),
                 0, wx.ALIGN_CENTER_VERTICAL)
        self.cost_ctrl = wx.Choice(self, choices=[l.name for l in self.terrain_map])
        if self.terrain_map:
            self.cost_ctrl.SetSelection(0)
        grid.Add(self.cost_ctrl, 0, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="Cost &Band:"),
                 0, wx.ALIGN_CENTER_VERTICAL)
        self.band_ctrl = wx.Choice(self, choices=["Band 1"])
        grid.Add(self.band_ctrl, 0, wx.EXPAND)

        grid.Add(wx.StaticText(self, label="&Connect method:"),
                 0, wx.ALIGN_CENTER_VERTICAL)
        self.method_ctrl = wx.Choice(self, choices=self.METHODS)
        self.method_ctrl.SetSelection(3)
        grid.Add(self.method_ctrl, 0, wx.EXPAND)

        self.pair_grid = wx.FlexGridSizer(cols=2, vgap=8, hgap=10)
        self.pair_grid.AddGrowableCol(1)
        self.pair_grid.Add(wx.StaticText(self, label="&From point:"),
                           0, wx.ALIGN_CENTER_VERTICAL)
        self.from_ctrl = wx.Choice(self, choices=[])
        self.pair_grid.Add(self.from_ctrl, 0, wx.EXPAND)
        self.pair_grid.Add(wx.StaticText(self, label="&To point:"),
                           0, wx.ALIGN_CENTER_VERTICAL)
        self.to_ctrl = wx.Choice(self, choices=[])
        self.pair_grid.Add(self.to_ctrl, 0, wx.EXPAND)

        self._sizer.Add(grid, 0, wx.ALL | wx.EXPAND, 10)
        self._sizer.Add(self.pair_grid, 0, wx.LEFT | wx.RIGHT | wx.EXPAND, 10)

        self.diag_cb = wx.CheckBox(self, label="Allow &diagonal moves (8-way)")
        self.diag_cb.SetValue(True)
        self._sizer.Add(self.diag_cb, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        self._sizer.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL),
                        0, wx.ALL | wx.EXPAND, 10)

        self.SetSizer(self._sizer)
        self._populate_band_ctrl()
        self._populate_points()
        self._update_mode_visibility()
        self.points_ctrl.Bind(wx.EVT_CHOICE, lambda e: self._populate_points())
        self.cost_ctrl.Bind(wx.EVT_CHOICE, lambda e: self._populate_band_ctrl())
        self.method_ctrl.Bind(wx.EVT_CHOICE, lambda e: self._update_mode_visibility())

    def _populate_band_ctrl(self):
        names = []
        active = 1
        idx = self.cost_ctrl.GetSelection()
        if 0 <= idx < len(self.terrain_map):
            layer = self.terrain_map[idx]
            names = (list(layer.band_names) if layer.band_names
                     else ["Band %d" % (i + 1) for i in range(layer.band_count)])
            active = layer.active_band
        self.band_ctrl.Clear()
        if names:
            self.band_ctrl.AppendItems(names)
            self.band_ctrl.SetSelection(max(0, min(active - 1, len(names) - 1)))

    def _populate_points(self):
        names = []
        if self.point_map:
            layer = self.point_map[self.points_ctrl.GetSelection()]
            names = [f.get_name() for f in layer.features]
        for ctrl in (self.from_ctrl, self.to_ctrl):
            ctrl.Clear()
            if names:
                ctrl.AppendItems(names)
                ctrl.SetSelection(0)
        if len(names) >= 2:
            self.to_ctrl.SetSelection(1)

    def _update_mode_visibility(self):
        is_pair = self.method_ctrl.GetSelection() == 0
        self._sizer.Show(self.pair_grid, is_pair)
        self.Layout()

    def get_data(self):
        points = (self.point_map[self.points_ctrl.GetSelection()]
                  if self.point_map else None)
        cost = (self.terrain_map[self.cost_ctrl.GetSelection()]
                if self.terrain_map else None)
        band = self.band_ctrl.GetSelection() + 1 if self.band_ctrl.GetCount() else 1
        method = ("pair", "chain", "star", "tree")[self.method_ctrl.GetSelection()]
        from_name = self.from_ctrl.GetStringSelection() if self.from_ctrl.GetCount() else None
        to_name = self.to_ctrl.GetStringSelection() if self.to_ctrl.GetCount() else None
        return points, cost, band, method, from_name, to_name, self.diag_cb.GetValue()


class TemplateDialog(wx.Dialog):
    def __init__(self, parent, title="Add Fields from Template"):
        super().__init__(parent, title=title, size=(460, 200))
        self.custom_templates = load_custom_templates()

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label="Choose a field template:"), 0, wx.ALL, 10)

        template_names = list(FIELD_TEMPLATES.keys())
        for cname in self.custom_templates:
            if cname not in template_names:
                template_names.append(cname)
        self.template_ctrl = wx.Choice(self, choices=template_names)
        self.template_ctrl.SetSelection(0)
        sizer.Add(self.template_ctrl, 0, wx.ALL | wx.EXPAND, 10)

        self.preview = wx.StaticText(self, label="")
        sizer.Add(self.preview, 0, wx.ALL | wx.EXPAND, 10)

        btn_sizer = wx.StdDialogButtonSizer()
        ok_btn = wx.Button(self, wx.ID_OK)
        cancel_btn = wx.Button(self, wx.ID_CANCEL)
        btn_sizer.AddButton(ok_btn)
        btn_sizer.AddButton(cancel_btn)
        btn_sizer.Realize()
        sizer.Add(btn_sizer, 0, wx.ALL | wx.EXPAND, 10)

        self.SetSizer(sizer)

        self.template_ctrl.Bind(wx.EVT_CHOICE, self.on_select)
        self.update_preview()

    def on_select(self, event):
        self.update_preview()

    def update_preview(self):
        name = self.template_ctrl.GetStringSelection()
        fields = self.get_template_fields()
        if fields:
            self.preview.SetLabel(f"{len(fields)} fields: " + ", ".join(fields.keys()))
        else:
            self.preview.SetLabel("No fields")

    def get_template_name(self):
        return self.template_ctrl.GetStringSelection()

    def get_template_fields(self):
        name = self.template_ctrl.GetStringSelection()
        if name in FIELD_TEMPLATES:
            return FIELD_TEMPLATES[name]
        tpl = self.custom_templates.get(name)
        if tpl:
            return {f["name"]: f["type"] for f in tpl.get("fields", [])}
        return {}


class TemplateNameDialog(wx.Dialog):
    def __init__(self, parent, title="Template Name", name="", description=""):
        super().__init__(parent, title=title, size=(440, 200))

        sizer = wx.BoxSizer(wx.VERTICAL)
        grid = wx.FlexGridSizer(2, 2, 8, 8)
        grid.AddGrowableCol(1)
        grid.Add(wx.StaticText(self, label="Template Name"))
        self.name_ctrl = wx.TextCtrl(self, value=name)
        grid.Add(self.name_ctrl, 0, wx.EXPAND)
        grid.Add(wx.StaticText(self, label="Description"))
        self.desc_ctrl = wx.TextCtrl(self, value=description)
        grid.Add(self.desc_ctrl, 0, wx.EXPAND)
        sizer.Add(grid, 0, wx.ALL | wx.EXPAND, 10)

        btn_sizer = wx.StdDialogButtonSizer()
        ok_btn = wx.Button(self, wx.ID_OK)
        cancel_btn = wx.Button(self, wx.ID_CANCEL)
        btn_sizer.AddButton(ok_btn)
        btn_sizer.AddButton(cancel_btn)
        btn_sizer.Realize()
        sizer.Add(btn_sizer, 0, wx.ALL | wx.EXPAND, 10)

        self.SetSizer(sizer)
        self.name_ctrl.SetFocus()

    def get_data(self):
        return self.name_ctrl.GetValue().strip(), self.desc_ctrl.GetValue().strip()


class TemplateLibraryDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="Custom Templates", size=(560, 420))
        self.templates = load_custom_templates()

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self,
                                label="Saved templates live in mmapper_templates.json and appear in the "
                                      "'Field Template' and 'Add Fields from Template' dialogs."),
                  0, wx.ALL | wx.EXPAND, 10)

        self.list_ctrl = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.list_ctrl.InsertColumn(0, "Name", width=220)
        self.list_ctrl.InsertColumn(1, "Kind", width=80)
        self.list_ctrl.InsertColumn(2, "Fields", width=80)
        sizer.Add(self.list_ctrl, 1, wx.ALL | wx.EXPAND, 10)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        export_btn = wx.Button(self, label="Export to JSON...")
        delete_btn = wx.Button(self, label="Delete")
        btn_row.Add(export_btn)
        btn_row.Add(delete_btn, 0, wx.LEFT, 5)
        sizer.Add(btn_row, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        btn_sizer = wx.StdDialogButtonSizer()
        close_btn = wx.Button(self, wx.ID_CLOSE)
        btn_sizer.AddButton(close_btn)
        btn_sizer.Realize()
        sizer.Add(btn_sizer, 0, wx.ALL | wx.EXPAND, 10)

        self.SetSizer(sizer)

        export_btn.Bind(wx.EVT_BUTTON, self.on_export)
        delete_btn.Bind(wx.EVT_BUTTON, self.on_delete)

        self.refresh()

    def refresh(self):
        self.list_ctrl.DeleteAllItems()
        for name, tpl in self.templates.items():
            idx = self.list_ctrl.GetItemCount()
            self.list_ctrl.InsertItem(idx, name)
            self.list_ctrl.SetItem(idx, 1, tpl.get("kind", "layer"))
            self.list_ctrl.SetItem(idx, 2, str(len(tpl.get("fields", []))))

    def _selected(self):
        idx = self.list_ctrl.GetNextSelected(-1)
        if idx < 0:
            return None, None
        name = self.list_ctrl.GetItemText(idx, 0)
        return name, self.templates.get(name)

    def on_export(self, event):
        name, tpl = self._selected()
        if tpl is None:
            wx.MessageBox("Select a template first.", "Export Template", wx.OK | wx.ICON_INFORMATION)
            return
        with wx.FileDialog(self, "Export Template (JSON)",
                           wildcard="JSON files (*.json)|*.json",
                           defaultFile=f"{name}.json",
                           style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) as fd:
            if fd.ShowModal() == wx.ID_CANCEL:
                return
            path = fd.GetPath()
        try:
            template_to_json(tpl, path)
        except Exception as e:
            wx.LogError(f"Cannot export template '{name}'.\nError: {e}")
            return

    def on_delete(self, event):
        name, _ = self._selected()
        if name is None:
            wx.MessageBox("Select a template first.", "Delete Template", wx.OK | wx.ICON_INFORMATION)
            return
        if wx.MessageBox(f"Delete custom template '{name}'?", "Delete Template",
                         wx.YES_NO | wx.ICON_QUESTION) != wx.YES:
            return
        self.templates.pop(name, None)
        try:
            save_custom_templates(self.templates)
        except OSError as e:
            wx.LogError(f"Cannot delete template '{name}'.\nError: {e}")
            return
        self.refresh()


class PositionDialog(wx.Dialog):
    def __init__(self, parent, selected_name):
        super().__init__(parent, title="Where should the new layer go?", size=(430, 200))
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label="Choose where the new layer will appear in the tree:"),
                  0, wx.ALL | wx.EXPAND, 10)
        self.radio = wx.RadioBox(
            self,
            choices=[f"Top level (sibling of: {selected_name})",
                     f"Child of the selected layer: {selected_name}"])
        self.radio.SetSelection(0)
        sizer.Add(self.radio, 0, wx.ALL | wx.EXPAND, 10)

        btn_sizer = wx.StdDialogButtonSizer()
        ok_btn = wx.Button(self, wx.ID_OK)
        cancel_btn = wx.Button(self, wx.ID_CANCEL)
        btn_sizer.AddButton(ok_btn)
        btn_sizer.AddButton(cancel_btn)
        btn_sizer.Realize()
        sizer.Add(btn_sizer, 0, wx.ALL | wx.EXPAND, 10)
        self.SetSizer(sizer)

    def is_child(self):
        return self.radio.GetSelection() == 1


class FieldListDialog(wx.Dialog):
    def __init__(self, parent, layer, feature=None, group=None):
        title = f"Fields List - {layer.name}" if layer is not None else f"Properties - {group.name}"
        super().__init__(parent, title=title,
                         size=(580, 480), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.layer = layer
        self.feature = feature
        self.group = group
        self._editor = None
        self._edit_row = -1
        self._edit_key = None

        sizer = wx.BoxSizer(wx.VERTICAL)

        if self.group is not None:
            sizer.Add(wx.StaticText(self, label=f"{group.name} - add properties and set their values "
                                                f"(e.g. length, terrain, temperature)."), 0, wx.ALL, 10)
        elif feature is not None:
            sizer.Add(wx.StaticText(self, label=f"Feature: {feature.get_name()}"), 0, wx.ALL, 10)
        elif isinstance(self.layer, MapLayer):
            sizer.Add(wx.StaticText(self, label="Choose a feature above to view and edit its values."),
                      0, wx.ALL, 10)
        else:
            sizer.Add(wx.StaticText(self, label="No feature selected - values shown empty. "
                                               "Select a feature in the main window to edit its values."),
                      0, wx.ALL, 10)

        self._feature_items = []
        self.feature_ctrl = None
        if self.group is None and isinstance(self.layer, MapLayer):
            feat_row = wx.BoxSizer(wx.HORIZONTAL)
            feat_row.Add(wx.StaticText(self, label="Feature:"), 0, wx.ALIGN_CENTER_VERTICAL)
            self.feature_ctrl = wx.ComboBox(self, style=wx.CB_READONLY)
            self.feature_ctrl.SetToolTip("Choose the feature whose values you want to view or edit")
            feat_row.Add(self.feature_ctrl, 1, wx.LEFT | wx.EXPAND, 5)
            sizer.Add(feat_row, 0, wx.ALL | wx.EXPAND, 10)
            self.feature_ctrl.Bind(wx.EVT_COMBOBOX, self._on_feature_changed)

        search_row = wx.BoxSizer(wx.HORIZONTAL)
        search_row.Add(wx.StaticText(self, label="Find field:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.search_ctrl = wx.TextCtrl(self)
        self.search_ctrl.SetToolTip("Type to filter the fields list")
        search_row.Add(self.search_ctrl, 1, wx.LEFT | wx.EXPAND, 5)
        sizer.Add(search_row, 0, wx.ALL | wx.EXPAND, 10)

        self.list_ctrl = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.LC_HRULES | wx.LC_VRULES)
        self.list_ctrl.InsertColumn(0, "Field", width=190)
        self.list_ctrl.InsertColumn(1, "Value", width=190)
        self.list_ctrl.InsertColumn(2, "Type", width=80)
        sizer.Add(self.list_ctrl, 1, wx.ALL | wx.EXPAND, 10)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        edit_val_btn = wx.Button(self, label="Edit Value")
        edit_field_btn = wx.Button(self, label="Edit Field")
        add_btn = wx.Button(self, label="Add Field")
        remove_btn = wx.Button(self, label="Remove Field")
        btn_row.Add(edit_val_btn)
        btn_row.Add(edit_field_btn, 0, wx.LEFT, 5)
        btn_row.Add(add_btn, 0, wx.LEFT, 5)
        btn_row.Add(remove_btn, 0, wx.LEFT, 5)
        sizer.Add(btn_row, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        self.hint = wx.StaticText(self, label="Tip: double-click a value (or press F2 / Enter) to edit it in place.")
        sizer.Add(self.hint, 0, wx.ALL | wx.EXPAND, 10)

        btn_sizer = wx.StdDialogButtonSizer()
        ok_btn = wx.Button(self, wx.ID_OK)
        cancel_btn = wx.Button(self, wx.ID_CANCEL)
        btn_sizer.AddButton(ok_btn)
        btn_sizer.AddButton(cancel_btn)
        btn_sizer.Realize()
        sizer.Add(btn_sizer, 0, wx.ALL | wx.EXPAND, 10)

        self.SetSizer(sizer)
        self.SetMinSize((480, 360))

        edit_val_btn.Bind(wx.EVT_BUTTON, self.on_edit_value)
        edit_field_btn.Bind(wx.EVT_BUTTON, self.on_edit_field)
        add_btn.Bind(wx.EVT_BUTTON, self.on_add_field)
        remove_btn.Bind(wx.EVT_BUTTON, self.on_remove_field)
        self.search_ctrl.Bind(wx.EVT_TEXT, self._on_search)
        self.list_ctrl.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_activate)
        self.list_ctrl.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_select)
        self.list_ctrl.Bind(wx.EVT_LIST_KEY_DOWN, self._on_list_key)

        self._populate_feature_ctrl()
        self.refresh_list()

    def _schema(self):
        if self.group is not None:
            return self.group.prop_types
        return self.layer.schema

    def _props(self):
        if self.group is not None:
            return self.group.properties
        return self.feature.properties if self.feature is not None else None

    def _populate_feature_ctrl(self):
        if self.feature_ctrl is None:
            return
        self.feature_ctrl.Clear()
        self._feature_items = list(self.layer.features)
        for f in self._feature_items:
            self.feature_ctrl.Append(f.get_name())
        if self._feature_items:
            target = 0
            if self.feature is not None and self.feature in self._feature_items:
                target = self._feature_items.index(self.feature)
            self.feature = self._feature_items[target]
            self.feature_ctrl.SetSelection(target)
        else:
            self.feature = None
            self.feature_ctrl.Append("(no features yet)")
            self.feature_ctrl.SetSelection(0)

    def _sync_feature_ctrl(self):
        if self.feature_ctrl is None or not self._feature_items:
            return
        idx = self.feature_ctrl.GetSelection()
        if 0 <= idx < len(self._feature_items):
            self.feature_ctrl.SetString(idx, self._feature_items[idx].get_name())

    def _on_feature_changed(self, event):
        if self._editor is not None:
            self._finish_edit(commit=True)
        idx = self.feature_ctrl.GetSelection()
        if self._feature_items and 0 <= idx < len(self._feature_items):
            self.feature = self._feature_items[idx]
        else:
            self.feature = None
        self.refresh_list()
        if self.feature is not None:
            self.hint.SetLabel(f"Editing values for: {self.feature.get_name()}")

    def _add_field(self, name, ftype, default):
        if self.group is not None:
            self.group.add_property(name, default, ftype)
        else:
            self.layer.add_field(name, ftype, default)

    def _remove_field(self, key):
        if self.group is not None:
            self.group.remove_property(key)
        else:
            self.layer.remove_field(key)

    def _rename_field(self, old, new, ntype):
        if self.group is not None:
            self.group.rename_property(old, new, ntype)
        else:
            self.layer.rename_field(old, new, ntype)

    def _collect_fields(self):
        fields = []
        schema = self._schema()
        props = self._props()
        for k in schema:
            if k not in fields:
                fields.append(k)
        if props is not None:
            for k in props:
                if k not in fields:
                    fields.append(k)
        return fields

    def refresh_list(self):
        self.list_ctrl.DeleteAllItems()
        search = self.search_ctrl.GetValue().strip().lower()
        schema = self._schema()
        props = self._props()
        idx = 0
        for k in self._collect_fields():
            if search and search not in k.lower():
                continue
            self.list_ctrl.InsertItem(idx, k)
            val = props.get(k) if props is not None else None
            self.list_ctrl.SetItem(idx, 1, "" if val is None else str(val))
            self.list_ctrl.SetItem(idx, 2, schema.get(k, "text"))
            idx += 1

    def _selected_field(self):
        idx = self.list_ctrl.GetFirstSelected()
        if idx < 0:
            return None
        return self.list_ctrl.GetItemText(idx, 0)

    def _select_field(self, name):
        for i in range(self.list_ctrl.GetItemCount()):
            if self.list_ctrl.GetItemText(i, 0) == name:
                self.list_ctrl.Select(i, on=1)
                self.list_ctrl.Focus(i)
                self.list_ctrl.EnsureVisible(i)
                break

    def _on_search(self, event):
        self.refresh_list()

    def _on_select(self, event):
        idx = event.GetIndex()
        key = self.list_ctrl.GetItemText(idx, 0)
        self.hint.SetLabel(f"Field: {key} - double-click its value (or press F2 / Enter) to edit it")

    def _on_activate(self, event):
        self._begin_edit_value(event.GetIndex())

    def _on_list_key(self, event):
        code = event.GetKeyCode()
        if code in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) or code == wx.WXK_F2:
            self._begin_edit_value(self.list_ctrl.GetFirstSelected())
            return
        event.Skip()

    def _begin_edit_value(self, row):
        if self._editor is not None:
            return
        if self._props() is None:
            if self.group is None:
                wx.MessageBox("Select a feature first to edit its values.", "Edit Value", wx.OK | wx.ICON_INFORMATION)
            return
        if row < 0 or row >= self.list_ctrl.GetItemCount():
            return
        key = self.list_ctrl.GetItemText(row, 0)
        ftype = self.list_ctrl.GetItemText(row, 2)
        rect = self.list_ctrl.GetItemRect(row)
        cell = wx.Rect(rect.x + self.list_ctrl.GetColumnWidth(0), rect.y,
                       self.list_ctrl.GetColumnWidth(1), rect.height)
        current = self._props().get(key)
        self._edit_row = row
        self._edit_key = key
        if ftype == "boolean":
            editor = wx.Choice(self.list_ctrl, choices=["True", "False"])
            editor.SetSelection(0 if current in (True, "True") else 1)
            editor.Bind(wx.EVT_CHOICE, self._on_choice)
        else:
            editor = wx.TextCtrl(self.list_ctrl, value="" if current is None else str(current),
                                 style=wx.TE_PROCESS_ENTER)
            editor.Bind(wx.EVT_TEXT_ENTER, self._on_commit)
            editor.Bind(wx.EVT_CHAR, self._on_editor_key)
        editor.SetPosition(cell.Position)
        editor.SetSize(cell.Size)
        editor.Bind(wx.EVT_KILL_FOCUS, self._on_kill_focus)
        self._editor = editor
        editor.SetFocus()
        if isinstance(editor, wx.TextCtrl):
            editor.SetSelection(-1, -1)

    def _on_editor_key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self._finish_edit(commit=False)
            return
        if event.GetKeyCode() == wx.WXK_TAB:
            self._finish_edit(commit=True)
            return
        event.Skip()

    def _on_commit(self, event):
        self._finish_edit(commit=True)

    def _on_choice(self, event):
        self._finish_edit(commit=True)

    def _on_kill_focus(self, event):
        if self._editor is not None:
            wx.CallAfter(self._finish_edit, True)

    def _finish_edit(self, commit=True):
        editor = self._editor
        self._editor = None
        if editor is None:
            return
        row = self._edit_row
        key = self._edit_key
        if commit and self._props() is not None and key is not None:
            if isinstance(editor, wx.TextCtrl):
                value = editor.GetValue()
            else:
                value = editor.GetStringSelection() == "True"
            self._set_value(key, value)
        editor.Destroy()
        self.refresh_list()
        self._sync_feature_ctrl()
        if row >= 0 and row < self.list_ctrl.GetItemCount():
            self.list_ctrl.Select(row, on=1)
            self.list_ctrl.SetFocus()

    def _set_value(self, key, value):
        ftype = self._schema().get(key, "text")
        props = self._props()
        if props is None:
            return
        if ftype == "number":
            try:
                props[key] = _parse_number(value)
            except (TypeError, ValueError):
                props[key] = value
        elif ftype == "integer":
            try:
                props[key] = int(_parse_number(value))
            except (TypeError, ValueError):
                props[key] = value
        elif ftype == "boolean":
            props[key] = value if isinstance(value, bool) \
                else str(value).lower() in ("1", "true", "yes", "y", "on")
        else:
            props[key] = value

    def on_edit_value(self, event):
        row = self.list_ctrl.GetFirstSelected()
        if row < 0:
            wx.MessageBox("Select a field first.", "Edit Value", wx.OK | wx.ICON_INFORMATION)
            return
        self._begin_edit_value(row)

    def on_edit_field(self, event):
        key = self._selected_field()
        if key is None:
            wx.MessageBox("Select a field first.", "Edit Field", wx.OK | wx.ICON_INFORMATION)
            return
        dlg = SingleFieldDialog(self, title="Edit Field", name=key, ftype=self._schema().get(key, "text"))
        if dlg.ShowModal() == wx.ID_OK:
            new_name, new_type, _ = dlg.get_data()
            if new_name:
                try:
                    self._rename_field(key, new_name, new_type)
                except ValueError as e:
                    wx.MessageBox(str(e), "Edit Field", wx.OK | wx.ICON_ERROR)
                    dlg.Destroy()
                    return
                self.refresh_list()
                self._select_field(new_name)
        dlg.Destroy()

    def on_add_field(self, event):
        dlg = SingleFieldDialog(self)
        if dlg.ShowModal() == wx.ID_OK:
            name, ftype, default = dlg.get_data()
            if name:
                self._add_field(name, ftype, default or None)
                self.refresh_list()
                self._select_field(name)
        dlg.Destroy()

    def on_remove_field(self, event):
        key = self._selected_field()
        if key is None:
            wx.MessageBox("Select a field first.", "Remove Field", wx.OK | wx.ICON_INFORMATION)
            return
        if wx.MessageBox(f"Remove field '{key}'?", "Remove Field",
                         wx.YES_NO | wx.ICON_QUESTION) == wx.YES:
            self._remove_field(key)
            self.refresh_list()


class MainFrame(wx.Frame):
    def __init__(self):
        super().__init__(parent=None, title='Accessible GIS Viewer', size=(840, 640))

        self.layers = []
        self.current_layer = None
        self.current_feature = None
        self.current_terrain = None
        self.current_project = None
        self._undo_stack = []
        self._redo_stack = []
        self._undo_enabled = True
        self.undo_item = None
        self.redo_item = None
        self._drag_item = None
        self._drag_layer = None
        self._updating_selection = False

        self.init_ui()
        self.init_menu()
        self.CreateStatusBar()
        self.Centre()
        self.Show()

    def init_ui(self):
        self.splitter = wx.SplitterWindow(self, style=wx.SP_3D | wx.SP_LIVE_UPDATE)

        self.tree_panel = wx.Panel(self.splitter)
        tree_sizer = wx.BoxSizer(wx.VERTICAL)

        self.tree = wx.TreeCtrl(self.tree_panel, style=wx.TR_DEFAULT_STYLE | wx.TR_HIDE_ROOT | wx.TR_HAS_BUTTONS)
        self.tree.Bind(wx.EVT_TREE_SEL_CHANGED, self.on_tree_select)
        self.tree.Bind(wx.EVT_TREE_ITEM_RIGHT_CLICK, self.on_tree_context_menu)
        self.tree.Bind(wx.EVT_TREE_BEGIN_DRAG, self.on_tree_begin_drag)
        self.tree.Bind(wx.EVT_TREE_END_DRAG, self.on_tree_end_drag)
        self.root_node = self.tree.AddRoot("Root")

        tree_sizer.Add(self.tree, 1, wx.EXPAND)
        self.tree_panel.SetSizer(tree_sizer)

        self.list_panel = wx.Panel(self.splitter)
        list_sizer = wx.BoxSizer(wx.VERTICAL)

        self.list_ctrl = wx.ListCtrl(self.list_panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.LC_HRULES | wx.LC_VRULES)
        self.list_ctrl.InsertColumn(0, "Property", width=240)
        self.list_ctrl.InsertColumn(1, "Value", width=460)

        self.list_ctrl.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_list_item_activated)

        list_sizer.Add(self.list_ctrl, 1, wx.EXPAND)
        self.list_panel.SetSizer(list_sizer)

        self.splitter.SetMinimumPaneSize(150)
        self.splitter.SplitHorizontally(self.tree_panel, self.list_panel, 300)

        self.map_canvas = MapCanvasPanel(self)

        self.main_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.main_sizer.Add(self.map_canvas, 1, wx.EXPAND)
        self.main_sizer.Add(self.splitter, 1, wx.EXPAND)
        self.SetSizer(self.main_sizer)

    def init_menu(self):
        menu_bar = wx.MenuBar()

        file_menu = wx.Menu()
        new_project_item = file_menu.Append(wx.ID_ANY, "&New Project\tCtrl+Alt+N", "Start a new empty project")
        open_project_item = file_menu.Append(wx.ID_ANY, "&Open Project...\tCtrl+Alt+O", "Open a saved project")
        save_project_item = file_menu.Append(wx.ID_ANY, "&Save Project\tCtrl+Alt+S", "Save the whole project (all layers, nesting and properties)")
        save_project_as_item = file_menu.Append(wx.ID_ANY, "Save Project &As...\tCtrl+Alt+Shift+S", "Save the whole project to a new file")
        file_menu.AppendSeparator()
        new_layer_item = file_menu.Append(wx.ID_ANY, "&New Layer...\tCtrl+Shift+N", "Create a new empty GeoJSON layer")
        file_menu.AppendSeparator()
        open_item = file_menu.Append(wx.ID_OPEN, "&Open GeoJSON...\tCtrl+O", "Open a GeoJSON file")
        add_vector_item = file_menu.Append(wx.ID_ANY, "&Add Vector Layer...\tCtrl+Shift+O", "Import a Shapefile, KML, GPKG, etc.")
        open_terrain_item = file_menu.Append(wx.ID_ANY, "Open &Terrain Raster...\tCtrl+T", "Open a terrain/elevation raster (GeoTIFF/DEM)")
        file_menu.AppendSeparator()
        save_item = file_menu.Append(wx.ID_SAVE, "&Save\tCtrl+S", "Save the current layer (GeoJSON or raster)")
        save_as_item = file_menu.Append(wx.ID_SAVEAS, "Save &As...\tCtrl+Shift+S", "Save the current layer to a new file")
        file_menu.AppendSeparator()
        export_html_item = file_menu.Append(wx.ID_ANY, "&Export Web Map (HTML)...\tCtrl+E", "Export the layer to an interactive HTML map")
        export_csv_item = file_menu.Append(wx.ID_ANY, "Export &Layer Table (CSV)...\tCtrl+D", "Export the layer's attribute table to CSV")
        file_menu.AppendSeparator()
        exit_item = file_menu.Append(wx.ID_EXIT, "E&xit\tCtrl+Q", "Exit application")
        menu_bar.Append(file_menu, "&File")

        edit_menu = wx.Menu()
        undo_item = edit_menu.Append(wx.ID_ANY, "&Undo\tCtrl+Z", "Undo the last change")
        redo_item = edit_menu.Append(wx.ID_ANY, "&Redo\tCtrl+Shift+Z", "Redo the last undone change")
        edit_menu.AppendSeparator()
        add_feature_item = edit_menu.Append(wx.ID_ANY, "&Add Feature...\tCtrl+N", "Add a new feature to the selected layer")
        edit_geometry_item = edit_menu.Append(wx.ID_ANY, "&Edit Geometry...\tCtrl+G", "Edit the selected feature's geometry")
        duplicate_feature_item = edit_menu.Append(wx.ID_ANY, "&Duplicate Feature\tCtrl+Shift+D", "Duplicate the selected feature")
        delete_feature_item = edit_menu.Append(wx.ID_ANY, "&Delete Feature\tDelete", "Delete the selected feature")
        edit_menu.AppendSeparator()
        add_property_item = edit_menu.Append(wx.ID_ANY, "Add/Edit &Property...\tCtrl+P", "Add or edit a property on the selected feature")
        copy_feature_item = edit_menu.Append(wx.ID_ANY, "&Copy Feature to Layer...\tCtrl+Shift+C", "Copy the selected feature to another layer")
        edit_menu.AppendSeparator()
        query_elev_item = edit_menu.Append(wx.ID_ANY, "Query &Elevation...\tCtrl+L", "Sample elevation from the selected terrain layer")
        menu_bar.Append(edit_menu, "&Edit")

        view_menu = wx.Menu()
        self.map_pane_item = view_menu.Append(wx.ID_ANY, "&Map Pane\tCtrl+M",
                                              "Show or hide the live map canvas",
                                              kind=wx.ITEM_CHECK)
        self.map_pane_item.Check(True)
        menu_bar.Append(view_menu, "&View")

        layer_menu = wx.Menu()
        layer_props_item = layer_menu.Append(wx.ID_ANY, "&Layer Properties...", "Add and edit properties on the selected layer itself (e.g. planet length, terrain)")
        show_hide_item = layer_menu.Append(wx.ID_ANY, "Show/&Hide Layer\tV", "Toggle whether the selected layer is visible in exports")
        layer_style_item = layer_menu.Append(wx.ID_ANY, "Layer &Style...\tCtrl+Shift+Y",
                                             "Customise how the layer is drawn on the map (colour, line width, fill, marker)")
        move_up_item = layer_menu.Append(wx.ID_ANY, "Move Layer &Up\tAlt+Up",
                                         "Move the selected layer earlier in the tree (drawn underneath)")
        move_down_item = layer_menu.Append(wx.ID_ANY, "Move Layer &Down\tAlt+Down",
                                           "Move the selected layer later in the tree (drawn on top)")
        layer_menu.AppendSeparator()
        remove_layer_item = layer_menu.Append(wx.ID_ANY, "&Remove Layer", "Remove the selected layer from the project")
        rename_layer_item = layer_menu.Append(wx.ID_ANY, "&Rename Layer...\tF2", "Rename the selected layer")
        duplicate_layer_item = layer_menu.Append(wx.ID_ANY, "&Duplicate Layer", "Create a copy of the selected layer")
        layer_menu.AppendSeparator()
        set_crs_item = layer_menu.Append(wx.ID_ANY, "&Set CRS...", "Assign a coordinate reference system to the layer")
        reproject_item = layer_menu.Append(wx.ID_ANY, "&Reproject Layer...", "Transform the layer to a new CRS")
        layer_menu.AppendSeparator()
        add_field_item = layer_menu.Append(wx.ID_ANY, "Add &Field...", "Add a field to the layer schema")
        remove_field_item = layer_menu.Append(wx.ID_ANY, "Remove &Field...", "Remove a field from the layer schema")
        add_template_item = layer_menu.Append(wx.ID_ANY, "Add &Fields from Template...", "Add common fields from a template")
        fields_list_item = layer_menu.Append(wx.ID_ANY, "&Fields List (View/Edit)...\tCtrl+F", "View all fields in a list and edit their values")
        layer_menu.AppendSeparator()
        export_layer_tpl_item = layer_menu.Append(wx.ID_ANY, "Export Layer as Template (JSON)...",
                                                  "Export the selected layer as a standardized JSON template")
        export_feature_tpl_item = layer_menu.Append(wx.ID_ANY, "Export Feature as Template (JSON)...",
                                                    "Export the selected feature as a standardized JSON template")
        import_tpl_item = layer_menu.Append(wx.ID_ANY, "Import Template (JSON)...",
                                            "Import a standardized JSON template (creates a layer or adds fields)")
        save_tpl_item = layer_menu.Append(wx.ID_ANY, "Save Layer/Feature as Custom Template...",
                                          "Save the current layer or feature as a reusable custom template")
        manage_tpl_item = layer_menu.Append(wx.ID_ANY, "Manage Custom Templates...",
                                            "List, export or delete the saved custom templates")
        layer_menu.AppendSeparator()
        geom_fields_item = layer_menu.Append(wx.ID_ANY, "Add &Area/Length Fields", "Compute area and length for every feature")
        centroid_item = layer_menu.Append(wx.ID_ANY, "Add &Centroid Field", "Compute the centroid of every feature")
        menu_bar.Append(layer_menu, "&Layer")

        raster_menu = wx.Menu()
        new_terrain_item = raster_menu.Append(wx.ID_ANY, "&New Terrain/Raster...\tCtrl+Shift+T", "Create a new terrain/raster layer")
        edit_raster_item = raster_menu.Append(wx.ID_ANY, "&Edit Raster Values...\tCtrl+R", "Fill, set a rectangle, add/multiply, invert, gradient, noise or smooth the selected raster")
        preview_raster_item = raster_menu.Append(wx.ID_ANY, "&Preview Raster (Image)...\tCtrl+Shift+P", "Show a matplotlib preview of the selected raster, with band selection")
        raster_style_item = raster_menu.Append(wx.ID_ANY, "Raster &Style...\tCtrl+Alt+M", "Choose the colour map used to draw the raster on the map")
        manage_bands_item = raster_menu.Append(wx.ID_ANY, "Manage &Bands...\tCtrl+Alt+B", "Add, duplicate, rename, remove or set the active band for the selected raster")
        menu_bar.Append(raster_menu, "&Raster")

        spatial_menu = wx.Menu()
        buffer_item = spatial_menu.Append(wx.ID_ANY, "&Buffer Selected Feature...", "Buffer the selected feature by a distance")
        simplify_item = spatial_menu.Append(wx.ID_ANY, "&Simplify Selected Feature...", "Simplify the selected feature's geometry")
        spatial_menu.AppendSeparator()
        split_sel_item = spatial_menu.Append(wx.ID_ANY, "Split Selected Feature &Coordinates...", "Split coordinates into X/Y fields")
        split_all_item = spatial_menu.Append(wx.ID_ANY, "Split &All Features Coordinates", "Add X/Y fields to every feature")
        spatial_menu.AppendSeparator()
        resource_suit_item = spatial_menu.Append(wx.ID_ANY, "&Resource Suitability Analysis...", "Score resource deposits by distance to infrastructure and terrain suitability")
        org_reach_item = spatial_menu.Append(wx.ID_ANY, "&Org Network Reach...", "Compute organisational influence zones (buffers, Voronoi, density) and corridor vulnerability")
        cost_net_item = spatial_menu.Append(wx.ID_ANY, "&Network Cost Surfaces...", "Connect points by least-cost paths over a raster cost surface")
        menu_bar.Append(spatial_menu, "&Spatial")

        self.SetMenuBar(menu_bar)
        self.undo_item = undo_item
        self.redo_item = redo_item
        self._update_undo_menus()

        self.Bind(wx.EVT_MENU, self.on_new_project, new_project_item)
        self.Bind(wx.EVT_MENU, self.on_open_project, open_project_item)
        self.Bind(wx.EVT_MENU, self.on_save_project, save_project_item)
        self.Bind(wx.EVT_MENU, self.on_save_project_as, save_project_as_item)
        self.Bind(wx.EVT_MENU, self.on_new_layer, new_layer_item)
        self.Bind(wx.EVT_MENU, self.on_open, open_item)
        self.Bind(wx.EVT_MENU, self.on_add_vector, add_vector_item)
        self.Bind(wx.EVT_MENU, self.on_open_terrain, open_terrain_item)
        self.Bind(wx.EVT_MENU, self.on_save, save_item)
        self.Bind(wx.EVT_MENU, self.on_save_as, save_as_item)
        self.Bind(wx.EVT_MENU, self.on_export_html, export_html_item)
        self.Bind(wx.EVT_MENU, self.on_export_csv, export_csv_item)
        self.Bind(wx.EVT_MENU, self.on_exit, exit_item)
        self.Bind(wx.EVT_MENU, self.on_undo, undo_item)
        self.Bind(wx.EVT_MENU, self.on_redo, redo_item)
        self.Bind(wx.EVT_MENU, self.on_add_feature, add_feature_item)
        self.Bind(wx.EVT_MENU, self.on_edit_geometry, edit_geometry_item)
        self.Bind(wx.EVT_MENU, self.on_duplicate_feature, duplicate_feature_item)
        self.Bind(wx.EVT_MENU, self.on_delete_feature, delete_feature_item)
        self.Bind(wx.EVT_MENU, self.on_add_property, add_property_item)
        self.Bind(wx.EVT_MENU, self.on_copy_feature, copy_feature_item)
        self.Bind(wx.EVT_MENU, self.on_query_elevation, query_elev_item)
        self.Bind(wx.EVT_MENU, self.on_toggle_map_pane, self.map_pane_item)
        self.Bind(wx.EVT_MENU, self.on_layer_properties, layer_props_item)
        self.Bind(wx.EVT_MENU, self.on_toggle_visibility, show_hide_item)
        self.Bind(wx.EVT_MENU, self.on_layer_style, layer_style_item)
        self.Bind(wx.EVT_MENU, self.on_move_layer_up, move_up_item)
        self.Bind(wx.EVT_MENU, self.on_move_layer_down, move_down_item)
        self.Bind(wx.EVT_MENU, self.on_remove_layer, remove_layer_item)
        self.Bind(wx.EVT_MENU, self.on_rename_layer, rename_layer_item)
        self.Bind(wx.EVT_MENU, self.on_duplicate_layer, duplicate_layer_item)
        self.Bind(wx.EVT_MENU, self.on_set_crs, set_crs_item)
        self.Bind(wx.EVT_MENU, self.on_reproject, reproject_item)
        self.Bind(wx.EVT_MENU, self.on_add_field_menu, add_field_item)
        self.Bind(wx.EVT_MENU, self.on_remove_field_menu, remove_field_item)
        self.Bind(wx.EVT_MENU, self.on_add_fields_template, add_template_item)
        self.Bind(wx.EVT_MENU, self.on_fields_list, fields_list_item)
        self.Bind(wx.EVT_MENU, self.on_export_layer_template, export_layer_tpl_item)
        self.Bind(wx.EVT_MENU, self.on_export_feature_template, export_feature_tpl_item)
        self.Bind(wx.EVT_MENU, self.on_import_template, import_tpl_item)
        self.Bind(wx.EVT_MENU, self.on_save_custom_template, save_tpl_item)
        self.Bind(wx.EVT_MENU, self.on_manage_templates, manage_tpl_item)
        self.Bind(wx.EVT_MENU, self.on_add_geometry_fields, geom_fields_item)
        self.Bind(wx.EVT_MENU, self.on_add_centroid_field, centroid_item)
        self.Bind(wx.EVT_MENU, self.on_new_terrain, new_terrain_item)
        self.Bind(wx.EVT_MENU, self.on_edit_raster, edit_raster_item)
        self.Bind(wx.EVT_MENU, self.on_preview_raster, preview_raster_item)
        self.Bind(wx.EVT_MENU, self.on_raster_style, raster_style_item)
        self.Bind(wx.EVT_MENU, self.on_manage_bands, manage_bands_item)
        self.Bind(wx.EVT_MENU, self.on_buffer, buffer_item)
        self.Bind(wx.EVT_MENU, self.on_simplify, simplify_item)
        self.Bind(wx.EVT_MENU, self.on_split_coordinates, split_sel_item)
        self.Bind(wx.EVT_MENU, self.on_split_all_coordinates, split_all_item)
        self.Bind(wx.EVT_MENU, self.on_resource_suitability, resource_suit_item)
        self.Bind(wx.EVT_MENU, self.on_org_network_reach, org_reach_item)
        self.Bind(wx.EVT_MENU, self.on_network_cost_surfaces, cost_net_item)

    def add_layer(self, layer, position=None):
        self.push_undo()
        if position is None:
            self.layers.append(layer)
        else:
            parent, as_child = position
            if as_child:
                parent.children.append(layer)
                layer.parent = parent
            else:
                target = parent.parent if parent.parent is not None else None
                if target is not None:
                    target.children.append(layer)
                    layer.parent = target
                else:
                    self.layers.append(layer)
        self._rebuild_tree()
        node = self._find_item_node(self.root_node, layer)
        if node.IsOk():
            self.tree.SelectItem(node)
        self.tree.SetFocus()

    def _ask_layer_position(self):
        item = self.tree.GetSelection()
        data = self.tree.GetItemData(item) if item.IsOk() else None
        if not isinstance(data, Layer):
            return None
        dlg = PositionDialog(self, data.name)
        position = None
        if dlg.ShowModal() == wx.ID_OK:
            position = (data, dlg.is_child())
        dlg.Destroy()
        return position

    def _create_tree_item(self, parent_node, item):
        node = self.tree.AppendItem(parent_node, self._layer_node_text(item))
        self.tree.SetItemData(node, item)
        if not item.visible:
            self.tree.SetItemTextColour(node, wx.Colour(128, 128, 128))
        if isinstance(item, TerrainLayer):
            self.tree.AppendItem(node, item.get_geometry_summary())
        else:
            for feature in item.features:
                fnode = self.tree.AppendItem(node, feature.get_name())
                self.tree.SetItemData(fnode, feature)
                if not item.visible:
                    self.tree.SetItemTextColour(fnode, wx.Colour(128, 128, 128))
        for child in item.children:
            self._create_tree_item(node, child)
        self.tree.Expand(node)
        return node

    def _rebuild_tree(self):
        self.tree.DeleteChildren(self.root_node)
        for item in self.layers:
            self._create_tree_item(self.root_node, item)
        self.refresh_map()

    def _find_item_node(self, parent, item):
        child, cookie = self.tree.GetFirstChild(parent)
        while child.IsOk():
            if self.tree.GetItemData(child) is item:
                return child
            found = self._find_item_node(child, item)
            if found.IsOk():
                return found
            child, cookie = self.tree.GetNextChild(parent, cookie)
        return wx.TreeItemId()

    def all_layers(self):
        result = []

        def walk(items):
            for it in items:
                if isinstance(it, Layer):
                    result.append(it)
                    walk(it.children)

        walk(self.layers)
        return result

    def _detach(self, item):
        if getattr(item, "parent", None) is not None:
            item.parent.children = [c for c in item.parent.children if c is not item]
            item.parent = None
        else:
            self.layers = [x for x in self.layers if x is not item]

    def _find_layer_node(self, layer):
        return self._find_item_node(self.root_node, layer)

    def _find_feature_node(self, feature):
        return self._find_item_node(self.root_node, feature)

    def _layer_for_feature(self, feature):
        for layer in self.all_layers():
            if isinstance(layer, MapLayer) and any(f is feature for f in layer.features):
                return layer
        return None

    def _refresh_layer_node(self, layer):
        node = self._find_layer_node(layer)
        if not node.IsOk():
            return
        self._apply_layer_node_text(node, layer)
        self.tree.DeleteChildren(node)
        if isinstance(layer, TerrainLayer):
            self.tree.AppendItem(node, layer.get_geometry_summary())
        else:
            for feature in layer.features:
                item = self.tree.AppendItem(node, feature.get_name())
                self.tree.SetItemData(item, feature)
                if not layer.visible:
                    self.tree.SetItemTextColour(item, wx.Colour(128, 128, 128))
        for child in layer.children:
            self._create_tree_item(node, child)
        self.tree.Expand(node)
        self.refresh_map()

    def refresh_map(self):
        if self.map_canvas.IsShown():
            self.map_canvas.update_legend()
            self.map_canvas.render()

    def _layer_node_text(self, layer):
        return f"{layer.name} (hidden)" if not layer.visible else layer.name

    def _apply_layer_node_text(self, node, layer):
        self.tree.SetItemText(node, self._layer_node_text(layer))
        self.tree.SetItemTextColour(node,
                                    wx.Colour(128, 128, 128) if not layer.visible else wx.BLACK)

    def on_new_project(self, event):
        if self.layers:
            if wx.MessageBox("Start a new project? The current project will be cleared.",
                             "New Project", wx.YES_NO | wx.ICON_QUESTION) != wx.YES:
                return
        self.push_undo()
        self.layers = []
        self.current_project = None
        self._clear_selection()
        self._rebuild_tree()
        self.SetStatusText("Started a new project")

    def on_open_project(self, event):
        with wx.FileDialog(self, "Open Project",
                           wildcard="Accessible GIS project (*.agis)|*.agis",
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dlg:
            if dlg.ShowModal() == wx.ID_CANCEL:
                return
            path = dlg.GetPath()
            try:
                layers = load_project(path)
            except Exception as e:
                wx.LogError(f"Cannot open project '{path}'.\nError: {e}")
                return
            if self.layers:
                if wx.MessageBox("Replace the current project with the loaded one?",
                                 "Open Project", wx.YES_NO | wx.ICON_QUESTION) != wx.YES:
                    return
            self.push_undo()
            self.layers = layers
            self.current_project = path
            self._clear_selection()
            self._rebuild_tree()
            self.SetStatusText(f"Opened project '{os.path.basename(path)}'")

    def on_save_project(self, event):
        if not self.layers:
            wx.MessageBox("Project is empty. Add some layers first.",
                          "Save Project", wx.OK | wx.ICON_INFORMATION)
            return
        if self.current_project:
            save_project(self.current_project, self.layers)
            self.SetStatusText(f"Saved project to {self.current_project}")
        else:
            self.on_save_project_as(event)

    def on_save_project_as(self, event):
        if not self.layers:
            wx.MessageBox("Project is empty. Add some layers first.",
                          "Save Project", wx.OK | wx.ICON_INFORMATION)
            return
        with wx.FileDialog(self, "Save Project",
                           wildcard="Accessible GIS project (*.agis)|*.agis",
                           style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
                           defaultFile="project.agis") as dlg:
            if dlg.ShowModal() == wx.ID_CANCEL:
                return
            path = dlg.GetPath()
            if not path.lower().endswith(".agis"):
                path += ".agis"
            try:
                save_project(path, self.layers)
            except Exception as e:
                wx.LogError(f"Cannot save project '{path}'.\nError: {e}")
                return
            self.current_project = path
            self.SetStatusText(f"Saved project to {path}")

    def on_new_layer(self, event):
        dlg = NewLayerDialog(self)
        if dlg.ShowModal() == wx.ID_OK:
            name, crs, fields = dlg.get_data()
            layer = MapLayer.new_empty(name, crs, fields)
            self.add_layer(layer, self._ask_layer_position())
            self.SetStatusText(f"Created layer '{name}'")
        dlg.Destroy()

    def on_open(self, event):
        with wx.FileDialog(self, "Open GeoJSON file",
                           wildcard="GeoJSON files (*.geojson;*.json)|*.geojson;*.json",
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as fileDialog:
            if fileDialog.ShowModal() == wx.ID_CANCEL:
                return
            pathname = fileDialog.GetPath()
            try:
                layer = MapLayer.from_geojson(pathname)
                self.add_layer(layer, self._ask_layer_position())
            except Exception as e:
                wx.LogError(f"Cannot open file '{pathname}'.\nError: {e}")

    def on_add_vector(self, event):
        with wx.FileDialog(self, "Add Vector Layer",
                           wildcard="Vector files (*.geojson;*.json;*.shp;*.kml;*.gpkg)|*.geojson;*.json;*.shp;*.kml;*.gpkg",
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dlg:
            if dlg.ShowModal() == wx.ID_CANCEL:
                return
            path = dlg.GetPath()
            try:
                layer = MapLayer.from_vector(path)
                self.add_layer(layer, self._ask_layer_position())
            except Exception as e:
                wx.LogError(f"Cannot open '{path}'.\nError: {e}")

    def on_open_terrain(self, event):
        with wx.FileDialog(self, "Open Terrain Raster",
                           wildcard="Raster files (*.tif;*.tiff;*.dem)|*.tif;*.tiff;*.dem",
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as fileDialog:
            if fileDialog.ShowModal() == wx.ID_CANCEL:
                return
            pathname = fileDialog.GetPath()
            try:
                layer = TerrainLayer(pathname)
                self.add_layer(layer, self._ask_layer_position())
            except Exception as e:
                wx.LogError(f"Cannot open terrain file '{pathname}'.\nError: {e}")

    def on_new_terrain(self, event):
        dlg = NewRasterDialog(self)
        if dlg.ShowModal() == wx.ID_OK:
            name, crs, width, height, bounds, value, bands = dlg.get_data()
            layer = TerrainLayer.new_empty(name=name, crs=crs, width=width, height=height,
                                           bounds=bounds, value=value, bands=bands)
            self.add_layer(layer, self._ask_layer_position())
            self.SetStatusText(f"Created raster '{name}'")
        dlg.Destroy()

    def on_edit_raster(self, event):
        if not isinstance(self.current_terrain, TerrainLayer):
            wx.MessageBox("Select a terrain/raster layer first.", "Edit Raster",
                          wx.OK | wx.ICON_INFORMATION)
            return
        dlg = RasterEditDialog(self, self.current_terrain)
        dlg.ShowModal()
        dlg.Destroy()
        self.populate_terrain(self.current_terrain)

    def on_preview_raster(self, event):
        if not isinstance(self.current_terrain, TerrainLayer):
            wx.MessageBox("Select a terrain/raster layer first.", "Preview Raster",
                          wx.OK | wx.ICON_INFORMATION)
            return
        dlg = RasterPreviewDialog(self, self.current_terrain)
        dlg.ShowModal()
        dlg.Destroy()
        self.populate_terrain(self.current_terrain)

    def on_raster_style(self, event):
        if not isinstance(self.current_terrain, TerrainLayer):
            wx.MessageBox("Select a terrain/raster layer first.", "Raster Style",
                          wx.OK | wx.ICON_INFORMATION)
            return
        dlg = RasterStyleDialog(self, self.current_terrain)
        if dlg.ShowModal() == wx.ID_OK:
            self.push_undo()
            self.current_terrain.set_style(dlg.get_style())
            self.populate_terrain(self.current_terrain)
            self.refresh_map()
            self.SetStatusText(f"Updated raster style for '{self.current_terrain.name}'")
        dlg.Destroy()

    def on_manage_bands(self, event):
        if not isinstance(self.current_terrain, TerrainLayer):
            wx.MessageBox("Select a terrain/raster layer first.", "Manage Bands",
                          wx.OK | wx.ICON_INFORMATION)
            return
        snapshot = self.capture_snapshot()
        dlg = BandManagerDialog(self, self.current_terrain)
        dlg.ShowModal()
        changed = dlg.changed
        dlg.Destroy()
        if changed:
            self.push_snapshot(snapshot)
        self.populate_terrain(self.current_terrain)
        self.refresh_map()
        if changed:
            self.SetStatusText(f"Updated bands for '{self.current_terrain.name}'")

    def on_save(self, event):
        if isinstance(self.current_terrain, TerrainLayer):
            terrain = self.current_terrain
            if terrain.filepath:
                terrain.save_to_geotiff(terrain.filepath)
                self.SetStatusText(f"Saved to {terrain.filepath}")
            else:
                self.on_save_as(event)
            return
        if not isinstance(self.current_layer, MapLayer):
            wx.MessageBox("Select a layer to save first.", "Save", wx.OK | wx.ICON_INFORMATION)
            return
        if self.current_layer.filepath:
            self.current_layer.save_to_geojson(self.current_layer.filepath)
            self.SetStatusText(f"Saved to {self.current_layer.filepath}")
        else:
            self.on_save_as(event)

    def on_save_as(self, event):
        if isinstance(self.current_terrain, TerrainLayer):
            terrain = self.current_terrain
            with wx.FileDialog(self, "Save Terrain Raster",
                               wildcard="GeoTIFF files (*.tif;*.tiff)|*.tif;*.tiff",
                               style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
                               defaultFile=terrain.name) as dlg:
                if dlg.ShowModal() == wx.ID_CANCEL:
                    return
                path = dlg.GetPath()
                if not path.lower().endswith((".tif", ".tiff")):
                    path += ".tif"
                terrain.save_to_geotiff(path)
                self.SetStatusText(f"Saved to {path}")
            return
        if not isinstance(self.current_layer, MapLayer):
            wx.MessageBox("Select a layer to save first.", "Save As", wx.OK | wx.ICON_INFORMATION)
            return
        with wx.FileDialog(self, "Save GeoJSON",
                           wildcard="GeoJSON files (*.geojson)|*.geojson",
                           style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
                           defaultFile=self.current_layer.name) as dlg:
            if dlg.ShowModal() == wx.ID_CANCEL:
                return
            path = dlg.GetPath()
            if not path.lower().endswith(".geojson"):
                path += ".geojson"
            self.current_layer.save_to_geojson(path)
            self.SetStatusText(f"Saved to {path}")

    def on_export_html(self, event):
        if not isinstance(self.current_layer, MapLayer):
            wx.MessageBox("Select a GeoJSON layer first.", "Export", wx.OK | wx.ICON_INFORMATION)
            return
        if not self.current_layer.features:
            wx.MessageBox("Layer has no features to export.", "Export", wx.OK | wx.ICON_INFORMATION)
            return
        with wx.FileDialog(self, "Export Web Map",
                           wildcard="HTML files (*.html)|*.html",
                           style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
                           defaultFile="map.html") as dlg:
            if dlg.ShowModal() == wx.ID_CANCEL:
                return
            path = dlg.GetPath()
            if not path.lower().endswith(".html"):
                path += ".html"
            try:
                self.export_web_map(path)
            except ValueError as e:
                wx.MessageBox(str(e), "Export", wx.OK | wx.ICON_INFORMATION)
                return
            self.SetStatusText(f"Exported web map to {path}")

    def export_web_map(self, path):
        import folium
        import geopandas as gpd

        visible = [l for l in self.all_layers()
                   if isinstance(l, MapLayer) and l.is_effectively_visible() and l.features]
        if not visible:
            raise ValueError("No visible layers with features to export. Unhide a layer first.")
        fc = {
            "type": "FeatureCollection",
            "features": [f.to_geojson() for l in visible for f in l.features],
        }
        gdf = gpd.GeoDataFrame.from_features(fc)
        gdf.set_crs(self.current_layer.crs or "EPSG:4326", inplace=True)
        bounds = gdf.total_bounds
        center = [(bounds[1] + bounds[3]) / 2, (bounds[0] + bounds[2]) / 2]
        m = folium.Map(location=center, zoom_start=10)
        for layer in visible:
            layer_fc = {
                "type": "FeatureCollection",
                "features": [f.to_geojson() for f in layer.features],
            }
            layer_gdf = gpd.GeoDataFrame.from_features(layer_fc)
            layer_gdf.set_crs(layer.crs or "EPSG:4326", inplace=True)
            folium.GeoJson(layer_gdf, name=layer.name).add_to(m)
        folium.LayerControl().add_to(m)
        m.save(path)

    def on_export_csv(self, event):
        if not isinstance(self.current_layer, MapLayer):
            wx.MessageBox("Select a GeoJSON layer first.", "Export CSV", wx.OK | wx.ICON_INFORMATION)
            return
        with wx.FileDialog(self, "Export Layer Table",
                           wildcard="CSV files (*.csv)|*.csv",
                           style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
                           defaultFile="layer.csv") as dlg:
            if dlg.ShowModal() == wx.ID_CANCEL:
                return
            path = dlg.GetPath()
            if not path.lower().endswith(".csv"):
                path += ".csv"
            self.current_layer.to_dataframe().to_csv(path, index=False)
            self.SetStatusText(f"Exported table to {path}")

    def on_add_feature(self, event):
        if not isinstance(self.current_layer, MapLayer):
            wx.MessageBox("Select a GeoJSON layer first.", "Add Feature", wx.OK | wx.ICON_INFORMATION)
            return
        dlg = AddFeatureDialog(self)
        if dlg.ShowModal() == wx.ID_OK:
            try:
                name, geom_type, coordinates, props = dlg.get_data()
            except ValueError as e:
                wx.MessageBox(str(e), "Add Feature", wx.OK | wx.ICON_ERROR)
                dlg.Destroy()
                return
            self.push_undo()
            feature = self.current_layer.add_feature(name, geom_type, coordinates, props)
            self._refresh_layer_node(self.current_layer)
            node = self._find_feature_node(feature)
            if node.IsOk():
                self.tree.SelectItem(node)
            self.SetStatusText(f"Added feature '{name}'")
        dlg.Destroy()

    def on_edit_geometry(self, event):
        if not self.current_feature:
            wx.MessageBox("Select a feature first.", "Edit Geometry", wx.OK | wx.ICON_INFORMATION)
            return
        dlg = GeometryEditDialog(self, self.current_feature.geometry)
        if dlg.ShowModal() == wx.ID_OK:
            try:
                geom = dlg.get_geometry()
            except Exception:
                wx.MessageBox("Invalid GeoJSON geometry.", "Edit Geometry", wx.OK | wx.ICON_ERROR)
                dlg.Destroy()
                return
            self.push_undo()
            self.current_feature.geometry = geom
            self.populate_feature(self.current_feature)
            self.SetStatusText("Geometry updated")
        dlg.Destroy()

    def on_duplicate_feature(self, event):
        if not self.current_feature:
            wx.MessageBox("Select a feature first.", "Duplicate Feature", wx.OK | wx.ICON_INFORMATION)
            return
        layer = self._layer_for_feature(self.current_feature)
        if not layer:
            return
        self.push_undo()
        new_feature = layer.duplicate_feature(self.current_feature)
        self._refresh_layer_node(layer)
        node = self._find_feature_node(new_feature)
        if node.IsOk():
            self.tree.SelectItem(node)
        self.SetStatusText("Duplicated feature")

    def on_delete_feature(self, event):
        if not self.current_feature:
            wx.MessageBox("Select a feature first.", "Delete Feature", wx.OK | wx.ICON_INFORMATION)
            return
        name = self.current_feature.get_name()
        if wx.MessageBox(f"Delete feature '{name}'?", "Delete Feature",
                         wx.YES_NO | wx.ICON_QUESTION) == wx.YES:
            self.push_undo()
            layer = self._layer_for_feature(self.current_feature)
            if layer:
                layer.remove_feature(self.current_feature)
                self._refresh_layer_node(layer)
            self.current_feature = None
            self.list_ctrl.DeleteAllItems()
            self.SetStatusText(f"Deleted feature '{name}'")

    def on_add_property(self, event):
        if not self.current_feature:
            wx.MessageBox("Select a feature first.", "Property", wx.OK | wx.ICON_INFORMATION)
            return
        dlg = PropertyDialog(self, "Add/Edit Property")
        if dlg.ShowModal() == wx.ID_OK:
            key, value = dlg.get_data()
            if not key.strip():
                dlg.Destroy()
                return
            self.push_undo()
            self.current_feature.properties[key.strip()] = value
            self.populate_feature(self.current_feature)
            node = self.tree.GetSelection()
            if node.IsOk() and key.strip().lower() in ("name", "title"):
                self.tree.SetItemText(node, self.current_feature.get_name())
            self.SetStatusText(f"Set {key.strip()} = {value}")
        dlg.Destroy()

    def on_copy_feature(self, event):
        if not self.current_feature:
            wx.MessageBox("Select a feature first.", "Copy Feature", wx.OK | wx.ICON_INFORMATION)
            return
        source = self._layer_for_feature(self.current_feature)
        dlg = LayerChoiceDialog(self, "Copy Feature To Layer", self.all_layers(), exclude=source)
        if dlg.ShowModal() == wx.ID_OK:
            target = dlg.get_selected()
            if target is None:
                dlg.Destroy()
                return
            self.push_undo()
            target.features.append(copy.deepcopy(self.current_feature))
            self._refresh_layer_node(target)
            self.SetStatusText(f"Copied feature to '{target.name}'")
        dlg.Destroy()

    def on_query_elevation(self, event):
        if not self.current_terrain:
            wx.MessageBox("Select a terrain layer first.", "Query Elevation", wx.OK | wx.ICON_INFORMATION)
            return
        dlg = wx.TextEntryDialog(self, "Enter longitude, latitude", "Query Elevation", "102.0, 0.5")
        if dlg.ShowModal() == wx.ID_OK:
            text = dlg.GetValue()
            try:
                lon, lat = [float(part) for part in text.split(",")]
            except ValueError:
                wx.MessageBox("Enter as: longitude, latitude", "Query Elevation", wx.OK | wx.ICON_ERROR)
                dlg.Destroy()
                return
            value = self.current_terrain.sample_elevation(lon, lat)
            if value is None:
                wx.MessageBox("No elevation data at that location.", "Query Elevation", wx.OK | wx.ICON_INFORMATION)
            else:
                wx.MessageBox(f"Elevation at ({lon}, {lat}) = {value:.2f} m", "Query Elevation", wx.OK | wx.ICON_INFORMATION)
        dlg.Destroy()

    def on_remove_layer(self, event):
        item = self.tree.GetSelection()
        data = self.tree.GetItemData(item) if item.IsOk() else None
        if data is None or isinstance(data, Feature):
            wx.MessageBox("Select a layer first.", "Remove Layer", wx.OK | wx.ICON_INFORMATION)
            return
        name = data.name
        detail = ""
        if isinstance(data, Layer) and data.children:
            detail = (f"\nThis layer has {len(data.children)} sub-layer(s). "
                      "Everything inside will be removed too.")
        if wx.MessageBox(f"Remove '{name}' from the project?{detail}", "Remove Layer",
                         wx.YES_NO | wx.ICON_QUESTION) == wx.YES:
            self.push_undo()
            self._detach(data)
            self._clear_selection()
            self._rebuild_tree()
            self.SetStatusText(f"Removed '{name}'")

    def on_layer_properties(self, event):
        if not isinstance(self.current_layer, Layer):
            wx.MessageBox("Select a layer first.", "Layer Properties", wx.OK | wx.ICON_INFORMATION)
            return
        self.push_undo()
        dlg = FieldListDialog(self, None, None, group=self.current_layer)
        if dlg.ShowModal() == wx.ID_OK:
            self.populate_layer_summary(self.current_layer)
        dlg.Destroy()

    def on_toggle_visibility(self, event):
        if not isinstance(self.current_layer, Layer):
            wx.MessageBox("Select a layer first.", "Show/Hide Layer", wx.OK | wx.ICON_INFORMATION)
            return
        self._toggle_visibility(self.current_layer)

    def _toggle_visibility(self, layer):
        self.push_undo()
        layer.visible = not layer.visible
        self._refresh_layer_node(layer)
        self.populate_layer_summary(layer)
        state = "hidden" if not layer.visible else "visible"
        self.SetStatusText(f"Layer '{layer.name}' is now {state}")

    def on_move_layer_up(self, event):
        if not isinstance(self.current_layer, Layer):
            wx.MessageBox("Select a layer first.", "Move Layer Up", wx.OK | wx.ICON_INFORMATION)
            return
        self._move_layer(self.current_layer, -1)

    def on_move_layer_down(self, event):
        if not isinstance(self.current_layer, Layer):
            wx.MessageBox("Select a layer first.", "Move Layer Down", wx.OK | wx.ICON_INFORMATION)
            return
        self._move_layer(self.current_layer, 1)

    def _move_layer(self, layer, delta):
        siblings = layer.parent.children if layer.parent is not None else self.layers
        if layer not in siblings:
            return False
        index = siblings.index(layer)
        new_index = index + delta
        if not (0 <= new_index < len(siblings)):
            return False
        self.push_undo()
        siblings.remove(layer)
        siblings.insert(new_index, layer)
        self._rebuild_tree()
        node = self._find_layer_node(layer)
        if node.IsOk():
            self.tree.SelectItem(node)
        self.refresh_map()
        direction = "up" if delta < 0 else "down"
        self.SetStatusText(f"Moved '{layer.name}' {direction}")
        return True

    def on_toggle_map_pane(self, event):
        show = self.map_pane_item.IsChecked()
        self.map_canvas.Show(show)
        if show:
            self.refresh_map()
        self.Layout()
        self.SetStatusText("Map Pane shown" if show else "Map Pane hidden")

    def on_layer_style(self, event):
        if not isinstance(self.current_layer, MapLayer):
            wx.MessageBox("Select a vector (GeoJSON) layer first.",
                          "Layer Style", wx.OK | wx.ICON_INFORMATION)
            return
        dlg = LayerStyleDialog(self, self.current_layer)
        if dlg.ShowModal() == wx.ID_OK:
            self.push_undo()
            self.current_layer.set_style(dlg.get_style())
            self.populate_layer_summary(self.current_layer)
            self.refresh_map()
            self.SetStatusText(f"Updated style for '{self.current_layer.name}'")
        dlg.Destroy()

    def _clear_selection(self):
        self.current_layer = None
        self.current_feature = None
        self.current_terrain = None
        self.list_ctrl.DeleteAllItems()

    def _snapshot(self):
        return copy.deepcopy(self.layers)

    def push_undo(self):
        self.push_snapshot(self._snapshot())

    def push_snapshot(self, snapshot):
        if not self._undo_enabled:
            return
        self._undo_stack.append(snapshot)
        if len(self._undo_stack) > 100:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._update_undo_menus()

    def capture_snapshot(self):
        return self._snapshot()

    def _restore(self, layers):
        self._undo_enabled = False
        try:
            self.layers = layers
            self._clear_selection()
            self._rebuild_tree()
        finally:
            self._undo_enabled = True
        self._update_undo_menus()

    def _update_undo_menus(self):
        if self.undo_item is not None:
            self.undo_item.Enable(bool(self._undo_stack))
        if self.redo_item is not None:
            self.redo_item.Enable(bool(self._redo_stack))

    def on_undo(self, event):
        if not self._undo_stack:
            return
        self._redo_stack.append(self._snapshot())
        self._restore(self._undo_stack.pop())
        self.SetStatusText("Undone")

    def on_redo(self, event):
        if not self._redo_stack:
            return
        self._undo_stack.append(self._snapshot())
        self._restore(self._redo_stack.pop())
        self.SetStatusText("Redone")

    def on_rename_layer(self, event):
        item = self.tree.GetSelection()
        data = self.tree.GetItemData(item) if item.IsOk() else None
        if data is None or isinstance(data, Feature):
            wx.MessageBox("Select a layer or group first.", "Rename Layer", wx.OK | wx.ICON_INFORMATION)
            return
        dlg = wx.TextEntryDialog(self, "New name:", "Rename", data.name)
        if dlg.ShowModal() == wx.ID_OK:
            name = dlg.GetValue().strip()
            if name:
                self.push_undo()
                data.name = name
                node = self._find_item_node(self.root_node, data)
                if node.IsOk():
                    self.tree.SetItemText(node, name)
        dlg.Destroy()

    def on_duplicate_layer(self, event):
        if not isinstance(self.current_layer, MapLayer):
            wx.MessageBox("Select a GeoJSON layer first.", "Duplicate Layer", wx.OK | wx.ICON_INFORMATION)
            return
        new_layer = copy.deepcopy(self.current_layer)
        new_layer.name = self.current_layer.name + " copy"
        new_layer.filepath = None
        self.add_layer(new_layer)
        self.SetStatusText(f"Duplicated layer '{self.current_layer.name}'")

    def on_set_crs(self, event):
        if not self.current_layer:
            wx.MessageBox("Select a layer first.", "Set CRS", wx.OK | wx.ICON_INFORMATION)
            return
        dlg = wx.TextEntryDialog(self, "Enter CRS (e.g. EPSG:4326):", "Set CRS",
                                 self.current_layer.crs or "EPSG:4326")
        if dlg.ShowModal() == wx.ID_OK:
            crs = dlg.GetValue().strip()
            if crs:
                try:
                    self.push_undo()
                    self.current_layer.set_crs(crs)
                    self._refresh_layer_node(self.current_layer)
                    self.populate_layer_summary(self.current_layer)
                except Exception as e:
                    wx.MessageBox(str(e), "Set CRS", wx.OK | wx.ICON_ERROR)
        dlg.Destroy()

    def on_reproject(self, event):
        if not isinstance(self.current_layer, MapLayer):
            wx.MessageBox("Select a GeoJSON layer first.", "Reproject", wx.OK | wx.ICON_INFORMATION)
            return
        if not self.current_layer.crs:
            wx.MessageBox("Layer has no CRS. Use Layer > Set CRS first.", "Reproject",
                          wx.OK | wx.ICON_INFORMATION)
            return
        dlg = wx.TextEntryDialog(self, "Target CRS (e.g. EPSG:3857):", "Reproject Layer", "EPSG:3857")
        if dlg.ShowModal() == wx.ID_OK:
            target = dlg.GetValue().strip()
            if target:
                try:
                    self.push_undo()
                    self.current_layer.reproject(target)
                    self._refresh_layer_node(self.current_layer)
                    self.populate_layer_summary(self.current_layer)
                    self.SetStatusText(f"Reprojected layer to {self.current_layer.crs}")
                except Exception as e:
                    wx.MessageBox(str(e), "Reproject", wx.OK | wx.ICON_ERROR)
        dlg.Destroy()

    def on_add_field_menu(self, event):
        if not isinstance(self.current_layer, MapLayer):
            wx.MessageBox("Select a GeoJSON layer first.", "Add Field", wx.OK | wx.ICON_INFORMATION)
            return
        dlg = SingleFieldDialog(self)
        if dlg.ShowModal() == wx.ID_OK:
            name, ftype, _ = dlg.get_data()
            if not name:
                dlg.Destroy()
                return
            self.push_undo()
            self.current_layer.add_field(name, ftype)
            self._refresh_layer_node(self.current_layer)
            self.populate_layer_summary(self.current_layer)
            self.SetStatusText(f"Added field '{name}'")
        dlg.Destroy()

    def on_remove_field_menu(self, event):
        if not isinstance(self.current_layer, MapLayer):
            wx.MessageBox("Select a GeoJSON layer first.", "Remove Field", wx.OK | wx.ICON_INFORMATION)
            return
        fields = list(self.current_layer.schema.keys())
        if not fields:
            fields = sorted({k for f in self.current_layer.features for k in f.properties})
        if not fields:
            wx.MessageBox("This layer has no fields.", "Remove Field", wx.OK | wx.ICON_INFORMATION)
            return
        dlg = wx.SingleChoiceDialog(self, "Field to remove:", "Remove Field", fields)
        if dlg.ShowModal() == wx.ID_OK:
            self.push_undo()
            self.current_layer.remove_field(dlg.GetStringSelection())
            self._refresh_layer_node(self.current_layer)
            self.populate_layer_summary(self.current_layer)
        dlg.Destroy()

    def on_add_geometry_fields(self, event):
        if not isinstance(self.current_layer, MapLayer):
            wx.MessageBox("Select a GeoJSON layer first.", "Add Fields", wx.OK | wx.ICON_INFORMATION)
            return
        self.push_undo()
        self.current_layer.add_geometry_fields("area", "length")
        self._refresh_layer_node(self.current_layer)
        self.populate_layer_summary(self.current_layer)
        self.SetStatusText("Added area/length fields")

    def on_add_centroid_field(self, event):
        if not isinstance(self.current_layer, MapLayer):
            wx.MessageBox("Select a GeoJSON layer first.", "Add Centroid", wx.OK | wx.ICON_INFORMATION)
            return
        self.push_undo()
        self.current_layer.add_centroid_field("centroid")
        self._refresh_layer_node(self.current_layer)
        self.populate_layer_summary(self.current_layer)
        self.SetStatusText("Added centroid field")

    def on_add_fields_template(self, event):
        if not isinstance(self.current_layer, MapLayer):
            wx.MessageBox("Select a GeoJSON layer first.", "Add Template", wx.OK | wx.ICON_INFORMATION)
            return
        dlg = TemplateDialog(self)
        if dlg.ShowModal() == wx.ID_OK:
            name = dlg.get_template_name()
            fields = dlg.get_template_fields()
            self.push_undo()
            for fname, ftype in fields.items():
                self.current_layer.add_field(fname, ftype)
            self._refresh_layer_node(self.current_layer)
            self.populate_layer_summary(self.current_layer)
            self.SetStatusText(f"Added {len(fields)} fields from template '{name}'")
        dlg.Destroy()

    def _current_map_layer(self):
        return self.current_layer if isinstance(self.current_layer, MapLayer) else None

    def _selected_feature_layer(self):
        if not self.current_feature:
            return None, None
        layer = self._layer_for_feature(self.current_feature)
        return layer, self.current_feature if layer is not None else None

    def on_export_layer_template(self, event):
        layer = self._current_map_layer()
        if layer is None:
            wx.MessageBox("Select a GeoJSON layer first.", "Export Layer Template",
                          wx.OK | wx.ICON_INFORMATION)
            return
        dlg = TemplateNameDialog(self, title="Export Layer Template", name=layer.name)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        tname, desc = dlg.get_data()
        dlg.Destroy()
        template = build_layer_template(layer, tname or layer.name, desc)
        with wx.FileDialog(self, "Export Layer Template (JSON)",
                           wildcard="JSON files (*.json)|*.json",
                           defaultFile=f"{tname or layer.name}.json",
                           style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) as fd:
            if fd.ShowModal() == wx.ID_CANCEL:
                return
            path = fd.GetPath()
        try:
            template_to_json(template, path)
        except Exception as e:
            wx.LogError(f"Cannot export layer template.\nError: {e}")
            return
        self.SetStatusText(f"Exported layer template to {path}")

    def on_export_feature_template(self, event):
        layer, feature = self._selected_feature_layer()
        if feature is None:
            wx.MessageBox("Select a feature first.", "Export Feature Template",
                          wx.OK | wx.ICON_INFORMATION)
            return
        dlg = TemplateNameDialog(self, title="Export Feature Template", name=feature.get_name())
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        tname, desc = dlg.get_data()
        dlg.Destroy()
        template = build_feature_template(layer, feature, tname or feature.get_name(), desc)
        with wx.FileDialog(self, "Export Feature Template (JSON)",
                           wildcard="JSON files (*.json)|*.json",
                           defaultFile=f"{tname or feature.get_name()}.json",
                           style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) as fd:
            if fd.ShowModal() == wx.ID_CANCEL:
                return
            path = fd.GetPath()
        try:
            template_to_json(template, path)
        except Exception as e:
            wx.LogError(f"Cannot export feature template.\nError: {e}")
            return
        self.SetStatusText(f"Exported feature template to {path}")

    def on_import_template(self, event):
        with wx.FileDialog(self, "Import Template (JSON)",
                           wildcard="JSON template files (*.json)|*.json",
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as fd:
            if fd.ShowModal() == wx.ID_CANCEL:
                return
            path = fd.GetPath()
        try:
            template = template_from_json(path)
        except Exception as e:
            wx.LogError(f"Cannot import template '{path}'.\nError: {e}")
            return
        if template.get("kind") == "layer":
            layer = create_layer_from_template(template)
            self.add_layer(layer, self._ask_layer_position())
            self.SetStatusText(f"Imported layer template '{template['name']}' ({len(layer.schema)} fields)")
            return
        target_layer = self._current_map_layer()
        target_feature = None
        if self.current_feature:
            flayer = self._layer_for_feature(self.current_feature)
            if flayer is not None:
                target_layer = flayer
                target_feature = self.current_feature
        if target_layer is None:
            wx.MessageBox("Select a GeoJSON layer (or a feature in one) to apply the feature template to.",
                          "Import Template", wx.OK | wx.ICON_INFORMATION)
            return
        self.push_undo()
        added = apply_feature_template(target_layer, target_feature, template)
        self._refresh_layer_node(target_layer)
        self.populate_layer_summary(target_layer)
        if target_feature is not None:
            self.populate_feature(target_feature)
        self.SetStatusText(f"Applied feature template '{template['name']}': {added} new field(s)")

    def on_save_custom_template(self, event):
        layer, feature = self._selected_feature_layer()
        if layer is None:
            layer = self._current_map_layer()
        if layer is None:
            wx.MessageBox("Select a GeoJSON layer (or a feature in one) first.", "Save Template",
                          wx.OK | wx.ICON_INFORMATION)
            return
        dlg = TemplateNameDialog(self, title="Save Custom Template",
                                 name=feature.get_name() if feature is not None else layer.name)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        tname, desc = dlg.get_data()
        dlg.Destroy()
        if feature is not None:
            template = build_feature_template(layer, feature, tname or feature.get_name(), desc)
        else:
            template = build_layer_template(layer, tname or layer.name, desc)
        custom = load_custom_templates()
        custom[template["name"]] = template
        try:
            save_custom_templates(custom)
        except OSError as e:
            wx.LogError(f"Cannot save template.\nError: {e}")
            return
        self.SetStatusText(f"Saved custom template '{template['name']}'")

    def on_manage_templates(self, event):
        dlg = TemplateLibraryDialog(self)
        dlg.ShowModal()
        dlg.Destroy()

    def on_fields_list(self, event):
        feature = None
        layer = self.current_layer
        if isinstance(self.current_feature, Feature):
            flayer = self._layer_for_feature(self.current_feature)
            if flayer is not None:
                layer = flayer
                feature = self.current_feature
        if not isinstance(layer, MapLayer):
            wx.MessageBox("Select a GeoJSON layer first.", "Fields List", wx.OK | wx.ICON_INFORMATION)
            return
        self.push_undo()
        dlg = FieldListDialog(self, layer, feature)
        if dlg.ShowModal() == wx.ID_OK:
            self._refresh_layer_node(layer)
            self.populate_layer_summary(layer)
            if self.current_feature:
                self.populate_feature(self.current_feature)
            self.SetStatusText("Fields updated")
        dlg.Destroy()

    def on_split_coordinates(self, event):
        if not self.current_feature:
            wx.MessageBox("Select a feature first.", "Split Coordinates", wx.OK | wx.ICON_INFORMATION)
            return
        layer = self._layer_for_feature(self.current_feature)
        if not layer:
            return
        dlg = SplitCoordinatesDialog(self, layer)
        if dlg.ShowModal() == wx.ID_OK:
            source, x_field, y_field = dlg.get_data()
            self.push_undo()
            if source == "__geometry__":
                ok = layer.split_geometry_coordinates(self.current_feature, x_field, y_field)
            else:
                ok = layer.split_property_coordinates(self.current_feature, source, x_field, y_field)
            if ok:
                self.populate_feature(self.current_feature)
                self.SetStatusText(f"Split {source} into {x_field}/{y_field}")
            else:
                wx.MessageBox("Could not split coordinates.", "Split Coordinates", wx.OK | wx.ICON_INFORMATION)
        dlg.Destroy()

    def on_split_all_coordinates(self, event):
        if not isinstance(self.current_layer, MapLayer):
            wx.MessageBox("Select a GeoJSON layer first.", "Split Coordinates", wx.OK | wx.ICON_INFORMATION)
            return
        self.push_undo()
        count = 0
        for f in self.current_layer.features:
            if self.current_layer.split_geometry_coordinates(f, "x", "y"):
                count += 1
        self._refresh_layer_node(self.current_layer)
        self.populate_layer_summary(self.current_layer)
        self.SetStatusText(f"Split coordinates for {count} features")

    def on_resource_suitability(self, event):
        map_layers = [l for l in self.all_layers() if isinstance(l, MapLayer)]
        terrain_layers = [l for l in self.all_layers() if isinstance(l, TerrainLayer)]
        if not map_layers:
            wx.MessageBox("Add at least one vector layer first.", "Resource Suitability",
                          wx.OK | wx.ICON_INFORMATION)
            return
        dlg = SuitabilityAnalysisDialog(self, map_layers, terrain_layers)
        if dlg.ShowModal() == wx.ID_OK:
            (deposits, corridors, terrain, max_km, max_slope,
             cost_layer, cost_band, cost_weight) = dlg.get_data()
            if deposits is None:
                wx.MessageBox("Select a deposit points layer.", "Resource Suitability",
                              wx.OK | wx.ICON_INFORMATION)
                dlg.Destroy()
                return
            engine = SpatialAnalysisEngine()
            self.push_undo()
            result = engine.compute_resource_suitability(
                deposits, transport_layers=[corridors] if corridors else [],
                terrain_layer=terrain, max_distance_km=max_km,
                max_slope_pct=max_slope, cost_layer=cost_layer,
                cost_band=cost_band, cost_weight=cost_weight)
            node = self._find_layer_node(deposits)
            if node.IsOk():
                self.tree.SelectItem(node)
            self._refresh_layer_node(deposits)
            self.refresh_map()
            weight_note = (f", reach weight {cost_weight:g}"
                           if cost_layer is not None else "")
            self.SetStatusText(
                f"Suitability: {result['analyzed']} deposits, "
                f"{result['excluded']} excluded, best score {result['best_score']:.2f} "
                f"({result['best']}){weight_note}")
        dlg.Destroy()

    def on_org_network_reach(self, event):
        map_layers = [l for l in self.all_layers() if isinstance(l, MapLayer)]
        if not map_layers:
            wx.MessageBox("Add at least one vector layer with organisation points first.",
                          "Org Network Reach", wx.OK | wx.ICON_INFORMATION)
            return
        dlg = OrgReachDialog(self, map_layers)
        if dlg.ShowModal() == wx.ID_OK:
            org_layer, method, rings, corridors, risk_km = dlg.get_data()
            if org_layer is None:
                wx.MessageBox("Select an organisation points layer.", "Org Network Reach",
                              wx.OK | wx.ICON_INFORMATION)
                dlg.Destroy()
                return
            engine = SpatialAnalysisEngine()
            self.push_undo()
            result = engine.generate_org_influence_zones(
                org_layer, method=method, buffer_rings_km=rings)
            polygons = result.get("polygons_layer")
            if polygons is not None and polygons.features:
                self.add_layer(polygons)
            node = self._find_layer_node(org_layer)
            if node.IsOk():
                self.tree.SelectItem(node)
            self._refresh_layer_node(org_layer)
            status = (f"Org influence ({method}): "
                      + ", ".join(f"{k}={v}" for k, v in
                                  result["control_zone_counts"].items()))
            if corridors is not None:
                vres = engine.calculate_corridor_vulnerability(
                    corridors, org_layer, risk_radius_km=risk_km)
                cnode = self._find_layer_node(corridors)
                if cnode.IsOk():
                    self.tree.SelectItem(cnode)
                self._refresh_layer_node(corridors)
                status += (f"; vulnerable corridors "
                           f"{vres['vulnerable']}/{vres['total']}")
            self.refresh_map()
            self.SetStatusText(status)
        dlg.Destroy()

    def on_network_cost_surfaces(self, event):
        map_layers = [l for l in self.all_layers() if isinstance(l, MapLayer)]
        terrain_layers = [l for l in self.all_layers() if isinstance(l, TerrainLayer)]
        if not map_layers:
            wx.MessageBox("Add at least one vector layer with points first.",
                          "Network Cost Surfaces", wx.OK | wx.ICON_INFORMATION)
            return
        if not terrain_layers:
            wx.MessageBox("Add at least one raster (cost surface) layer first.",
                          "Network Cost Surfaces", wx.OK | wx.ICON_INFORMATION)
            return
        dlg = NetworkCostDialog(self, map_layers, terrain_layers)
        if dlg.ShowModal() == wx.ID_OK:
            points, cost, band, method, from_name, to_name, diagonal = dlg.get_data()
            if points is None or cost is None:
                wx.MessageBox("Select a points layer and a cost surface layer.",
                              "Network Cost Surfaces", wx.OK | wx.ICON_INFORMATION)
                dlg.Destroy()
                return
            engine = SpatialAnalysisEngine()
            self.push_undo()
            if method == "pair":
                if not from_name or not to_name or from_name == to_name:
                    wx.MessageBox("Choose two different points for a pair path.",
                                  "Network Cost Surfaces", wx.OK | wx.ICON_INFORMATION)
                    dlg.Destroy()
                    return
                start = _feature_lonlat(points, from_name)
                end = _feature_lonlat(points, to_name)
                if start is None or end is None:
                    wx.MessageBox("Could not locate the chosen points.",
                                  "Network Cost Surfaces", wx.OK | wx.ICON_INFORMATION)
                    dlg.Destroy()
                    return
                result = engine.least_cost_path(cost, start, end,
                                                band=band, diagonal=diagonal)
                if not result["success"]:
                    wx.MessageBox(result.get("error") or "No path found.",
                                  "Network Cost Surfaces", wx.OK | wx.ICON_WARNING)
                    dlg.Destroy()
                    return
                line_layer = MapLayer.new_empty(
                    "Cost Path %s to %s" % (from_name, to_name), crs="EPSG:4326",
                    schema={"from_node": "text", "to_node": "text",
                            "path_cost": "number", "path_distance_km": "number",
                            "cells": "number"})
                line_layer.add_feature(
                    "%s to %s" % (from_name, to_name), "LineString",
                    [list(c) for c in result["path"]],
                    {"from_node": from_name, "to_node": to_name,
                     "path_cost": round(result["cumulative_cost"], 4),
                     "path_distance_km": round(result["distance_km"], 4),
                     "cells": result["cells"]})
                self.add_layer(line_layer)
                self.refresh_map()
                self.SetStatusText(
                    f"Cost path {from_name} to {to_name}: "
                    f"cost {result['cumulative_cost']:.2f}, "
                    f"{result['distance_km']:.2f} km, {result['cells']} cells")
            else:
                layer, summary = engine.least_cost_network(
                    cost, points, mode=method, band=band, diagonal=diagonal)
                if layer is None:
                    wx.MessageBox("Could not build a network; check the cost raster "
                                  "and that traversable paths connect the points.",
                                  "Network Cost Surfaces", wx.OK | wx.ICON_WARNING)
                    dlg.Destroy()
                    return
                self.add_layer(layer)
                self.refresh_map()
                self.SetStatusText(
                    f"Cost network ({summary['mode']}): {summary['paths']} paths, "
                    f"total cost {summary['total_cost']:.2f}, "
                    f"{summary['total_distance_km']:.2f} km")
        dlg.Destroy()

    def on_buffer(self, event):
        if not self.current_feature:
            wx.MessageBox("Select a feature first.", "Buffer", wx.OK | wx.ICON_INFORMATION)
            return
        layer = self._layer_for_feature(self.current_feature)
        if not layer:
            return
        dlg = wx.TextEntryDialog(self, "Buffer distance:", "Buffer Feature", "0.01")
        if dlg.ShowModal() == wx.ID_OK:
            try:
                distance = float(dlg.GetValue())
            except ValueError:
                wx.MessageBox("Enter a number.", "Buffer", wx.OK | wx.ICON_ERROR)
                dlg.Destroy()
                return
            self.push_undo()
            layer.buffer_feature(self.current_feature, distance)
            self._refresh_layer_node(layer)
            self.populate_feature(self.current_feature)
            self.SetStatusText(f"Buffered feature by {distance}")
        dlg.Destroy()

    def on_simplify(self, event):
        if not self.current_feature:
            wx.MessageBox("Select a feature first.", "Simplify", wx.OK | wx.ICON_INFORMATION)
            return
        layer = self._layer_for_feature(self.current_feature)
        if not layer:
            return
        dlg = wx.TextEntryDialog(self, "Simplify tolerance:", "Simplify Feature", "0.001")
        if dlg.ShowModal() == wx.ID_OK:
            try:
                tolerance = float(dlg.GetValue())
            except ValueError:
                wx.MessageBox("Enter a number.", "Simplify", wx.OK | wx.ICON_ERROR)
                dlg.Destroy()
                return
            self.push_undo()
            layer.simplify_feature(self.current_feature, tolerance)
            self._refresh_layer_node(layer)
            self.populate_feature(self.current_feature)
            self.SetStatusText(f"Simplified feature by {tolerance}")
        dlg.Destroy()

    def on_tree_select(self, event):
        item = event.GetItem()
        if not item.IsOk():
            return
        try:
            data = self.tree.GetItemData(item)
        except RuntimeError:
            return
        self.list_ctrl.DeleteAllItems()
        self.current_layer = None
        self.current_feature = None
        self.current_terrain = None

        if data is None:
            return
        if isinstance(data, TerrainLayer):
            self.current_terrain = data
            self.populate_terrain(data)
        elif isinstance(data, MapLayer):
            self.current_layer = data
            self.populate_layer_summary(data)
        elif isinstance(data, Feature):
            self.current_feature = data
            self.populate_feature(data)
        if not self._updating_selection:
            self.map_canvas.select_layer(data)

    def _append_menu_item(self, menu, label, handler):
        item = menu.Append(wx.ID_ANY, label)
        self.Bind(wx.EVT_MENU, handler, item)
        return item

    def on_tree_context_menu(self, event):
        item = event.GetItem()
        if not item.IsOk():
            return
        data = self.tree.GetItemData(item)
        if not isinstance(data, (Layer, Feature)):
            return
        self.tree.SelectItem(item)
        menu = wx.Menu()
        if isinstance(data, Feature):
            self._append_menu_item(menu, "&Edit Geometry...", self.on_edit_geometry)
            self._append_menu_item(menu, "&Duplicate Feature", self.on_duplicate_feature)
            self._append_menu_item(menu, "&Delete Feature", self.on_delete_feature)
            menu.AppendSeparator()
            self._append_menu_item(menu, "Add/Edit &Property...", self.on_add_property)
            self._append_menu_item(menu, "&Copy Feature to Layer...", self.on_copy_feature)
            menu.AppendSeparator()
            self._append_menu_item(menu, "&Buffer Feature...", self.on_buffer)
            self._append_menu_item(menu, "&Simplify Feature...", self.on_simplify)
            self._append_menu_item(menu, "Split Feature &Coordinates...", self.on_split_coordinates)
            menu.AppendSeparator()
            self._append_menu_item(menu, "Export Feature as Template (JSON)...", self.on_export_feature_template)
            self._append_menu_item(menu, "Save Feature as Custom Template...", self.on_save_custom_template)
        else:
            if isinstance(data, MapLayer):
                self._append_menu_item(menu, "&Add Feature...", self.on_add_feature)
            if isinstance(data, TerrainLayer):
                self._append_menu_item(menu, "&Preview Raster (Image)...", self.on_preview_raster)
                self._append_menu_item(menu, "&Edit Raster Values...", self.on_edit_raster)
                self._append_menu_item(menu, "Raster &Style...", self.on_raster_style)
                self._append_menu_item(menu, "Manage &Bands...", self.on_manage_bands)
                self._append_menu_item(menu, "&Query Elevation...", self.on_query_elevation)
            menu.AppendSeparator()
            self._append_menu_item(menu, "&Hide Layer" if data.visible else "&Show Layer",
                                   self.on_toggle_visibility)
            if isinstance(data, MapLayer):
                self._append_menu_item(menu, "&Style Layer...", self.on_layer_style)
            self._append_menu_item(menu, "Move Layer &Up", self.on_move_layer_up)
            self._append_menu_item(menu, "Move Layer &Down", self.on_move_layer_down)
            self._append_menu_item(menu, "&Layer Properties...", self.on_layer_properties)
            self._append_menu_item(menu, "&Rename Layer...", self.on_rename_layer)
            self._append_menu_item(menu, "&Duplicate Layer", self.on_duplicate_layer)
            self._append_menu_item(menu, "&Remove Layer", self.on_remove_layer)
            if isinstance(data, MapLayer):
                menu.AppendSeparator()
                self._append_menu_item(menu, "&Set CRS...", self.on_set_crs)
                self._append_menu_item(menu, "&Reproject Layer...", self.on_reproject)
                menu.AppendSeparator()
                self._append_menu_item(menu, "Add &Field...", self.on_add_field_menu)
                self._append_menu_item(menu, "Remove &Field...", self.on_remove_field_menu)
                self._append_menu_item(menu, "Add &Fields from Template...", self.on_add_fields_template)
                self._append_menu_item(menu, "&Fields List (View/Edit)...", self.on_fields_list)
                self._append_menu_item(menu, "Add &Area/Length Fields", self.on_add_geometry_fields)
                self._append_menu_item(menu, "Add &Centroid Field", self.on_add_centroid_field)
                menu.AppendSeparator()
                self._append_menu_item(menu, "Export Layer as Template (JSON)...", self.on_export_layer_template)
                self._append_menu_item(menu, "Export Feature as Template (JSON)...", self.on_export_feature_template)
                self._append_menu_item(menu, "Import Template (JSON)...", self.on_import_template)
                self._append_menu_item(menu, "Save Layer/Feature as Custom Template...", self.on_save_custom_template)
                menu.AppendSeparator()
                self._append_menu_item(menu, "&Export Web Map (HTML)...", self.on_export_html)
                self._append_menu_item(menu, "Export &Layer Table (CSV)...", self.on_export_csv)
            menu.AppendSeparator()
            self._append_menu_item(menu, "&Save", self.on_save)
            self._append_menu_item(menu, "Save &As...", self.on_save_as)
        self.tree.PopupMenu(menu)
        menu.Destroy()

    def on_tree_begin_drag(self, event):
        item = event.GetItem()
        if not item.IsOk():
            return
        data = self.tree.GetItemData(item)
        if not isinstance(data, Layer):
            return
        self._drag_item = item
        self._drag_layer = data
        event.Allow()

    def _drop_target_layer(self, item):
        data = self.tree.GetItemData(item)
        if isinstance(data, Layer):
            return data
        parent = self.tree.GetItemParent(item)
        if not parent.IsOk():
            return None
        data = self.tree.GetItemData(parent)
        return data if isinstance(data, Layer) else None

    def on_tree_end_drag(self, event):
        target = event.GetItem()
        dragged = self._drag_layer
        self._drag_item = None
        self._drag_layer = None
        if dragged is None or not target.IsOk():
            return
        if target == self.root_node:
            if dragged.parent is None:
                return
            self.push_undo()
            self._detach(dragged)
            self.layers.append(dragged)
            self._rebuild_tree()
            node = self._find_item_node(self.root_node, dragged)
            if node.IsOk():
                self.tree.SelectItem(node)
            self.SetStatusText(f"Moved '{dragged.name}' to top level")
            return
        target_layer = self._drop_target_layer(target)
        if target_layer is None or dragged is target_layer:
            return
        if dragged.contains(target_layer):
            wx.MessageBox("Cannot drop a layer into itself or one of its own sub-layers.",
                          "Re-nest", wx.OK | wx.ICON_INFORMATION)
            return
        if dragged.parent is target_layer:
            return
        self.push_undo()
        self._detach(dragged)
        dragged.parent = target_layer
        target_layer.children.append(dragged)
        self._rebuild_tree()
        node = self._find_item_node(self.root_node, dragged)
        if node.IsOk():
            self.tree.SelectItem(node)
        self.SetStatusText(f"Moved '{dragged.name}' under '{target_layer.name}'")

    def populate_feature(self, feature):
        self.list_ctrl.DeleteAllItems()
        index = 0
        for key, value in feature.properties.items():
            self.list_ctrl.InsertItem(index, str(key))
            self.list_ctrl.SetItem(index, 1, str(value))
            index += 1
        self.list_ctrl.InsertItem(index, "Geometry")
        self.list_ctrl.SetItem(index, 1, feature.get_geometry_summary())
        self.current_layer = self._layer_for_feature(feature)
        self.SetStatusText(f"Feature: {feature.get_name()}")

    def populate_layer_summary(self, layer):
        self.list_ctrl.DeleteAllItems()
        rows = [
            ("Name", layer.name),
            ("Visible", "Yes" if layer.visible else "No (hidden)"),
            ("Style", self._style_summary(layer)),
            ("File", layer.filepath or "Not saved yet"),
            ("CRS", layer.crs or "None"),
            ("Feature Count", str(len(layer.features))),
            ("Fields", ", ".join(layer.schema.keys()) if layer.schema else "None"),
        ]
        if layer.children:
            rows.append(("Sub-Layers", str(len(layer.children))))
        index = 0
        for key, value in rows:
            self.list_ctrl.InsertItem(index, key)
            self.list_ctrl.SetItem(index, 1, value)
            index += 1
        for key, value in layer.get_attribute_summary().items():
            self.list_ctrl.InsertItem(index, key)
            self.list_ctrl.SetItem(index, 1, str(value))
            index += 1
        for key, value in layer.get_geographic_summary().items():
            self.list_ctrl.InsertItem(index, key)
            self.list_ctrl.SetItem(index, 1, str(value))
            index += 1
        self._populate_layer_properties(layer, index)
        self.SetStatusText(f"Layer: {layer.name}")

    def _style_summary(self, layer):
        s = layer.style_with_defaults()
        mode = s.get("style_mode", "single")
        if mode == "graduated":
            return (f"Graduated by '{s['attribute_field']}' / {s['colormap']} / "
                    f"{s['class_count']} classes / {s['color']}")
        if mode == "categorised":
            return (f"Categorised by '{s['attribute_field']}' / {s['colormap']} / "
                    f"{s['color']}")
        return (f"{s['color']} / {s['marker']} {s['marker_size']} / "
                f"line {s['line_width']} / fill {s['fill_alpha']}")

    def _populate_layer_properties(self, layer, index):
        shown = set()
        keys = list(layer.prop_types.keys()) + [k for k in layer.properties if k not in layer.prop_types]
        for key in keys:
            if key in shown:
                continue
            shown.add(key)
            self.list_ctrl.InsertItem(index, key)
            value = layer.properties.get(key, "")
            if isinstance(value, (list, tuple)):
                value = ", ".join(str(v) for v in value)
            self.list_ctrl.SetItem(index, 1, str(value))
            index += 1

    def populate_terrain(self, terrain):
        self.list_ctrl.DeleteAllItems()
        active_name = ""
        if terrain.band_names and 1 <= terrain.active_band <= len(terrain.band_names):
            active_name = f" ({terrain.band_names[terrain.active_band - 1]})"
        style = terrain.style_with_defaults()
        if style.get("render_mode") == "rgb":
            rgb = style.get("rgb_bands") or [1, 2, 3]
            style_text = f"RGB composite (bands {rgb[0]}, {rgb[1]}, {rgb[2]})"
        else:
            style_text = f"Colour map: {style.get('colormap', 'terrain')}"
        rows = [
            ("Name", terrain.name),
            ("Visible", "Yes" if terrain.visible else "No (hidden)"),
            ("File", terrain.filepath or "Not saved yet"),
            ("CRS", terrain.crs or "None"),
            ("Bounds", terrain.get_geometry_summary()),
            ("Resolution", f"{terrain.resolution[0]:.4f}, {terrain.resolution[1]:.4f}"),
            ("Size", f"{terrain.height} x {terrain.width} cells"),
            ("Bands", str(terrain.band_count)),
            ("Active Band", f"{terrain.active_band} of {terrain.band_count}{active_name}"),
            ("Style", style_text),
        ]
        if getattr(terrain, "reduced", False) and terrain.original_shape is not None:
            rows.append(("Original Size", f"{terrain.original_shape[0]} x "
                                           f"{terrain.original_shape[1]} cells (decimated at load)"))
        if terrain.children:
            rows.append(("Sub-Layers", str(len(terrain.children))))
        index = 0
        for key, value in rows:
            self.list_ctrl.InsertItem(index, key)
            self.list_ctrl.SetItem(index, 1, value)
            index += 1
        for key, value in terrain.stats.items():
            self.list_ctrl.InsertItem(index, key)
            self.list_ctrl.SetItem(index, 1, str(value))
            index += 1
        self._populate_layer_properties(terrain, index)
        self.SetStatusText(f"Terrain: {terrain.name}")

    def on_list_item_activated(self, event):
        index = event.GetIndex()
        key = self.list_ctrl.GetItemText(index, col=0)
        value = self.list_ctrl.GetItemText(index, col=1)

        if (self.current_feature is None and isinstance(self.current_layer, Layer)
                and (key in self.current_layer.properties or key in self.current_layer.prop_types)):
            dlg = DetailDialog(self, "Edit/View Layer Property", key, value)
            if dlg.ShowModal() == wx.ID_OK:
                new_value = dlg.get_value()
                self.push_undo()
                self._set_layer_property(self.current_layer, key, new_value)
                if isinstance(self.current_layer, MapLayer):
                    self.populate_layer_summary(self.current_layer)
                elif isinstance(self.current_layer, TerrainLayer):
                    self.populate_terrain(self.current_layer)
            dlg.Destroy()
            return

        if not self.current_feature:
            wx.MessageBox("Select a feature to edit its properties.", "Edit", wx.OK | wx.ICON_INFORMATION)
            return
        if key == "Geometry":
            wx.MessageBox("Geometry editing is not supported in this view.", "Info", wx.OK | wx.ICON_INFORMATION)
            return

        dlg = DetailDialog(self, "Edit/View Property", key, value)
        if dlg.ShowModal() == wx.ID_OK:
            new_value = dlg.get_value()
            self.push_undo()
            self.list_ctrl.SetItem(index, 1, new_value)
            self.current_feature.properties[key] = new_value
            if key.lower() in ("name", "title"):
                node = self.tree.GetSelection()
                if node.IsOk():
                    self.tree.SetItemText(node, self.current_feature.get_name())
        dlg.Destroy()

    def _set_layer_property(self, layer, key, value):
        ftype = layer.prop_types.get(key, "text")
        if ftype == "number":
            try:
                layer.properties[key] = float(value)
            except (TypeError, ValueError):
                layer.properties[key] = value
        elif ftype == "integer":
            try:
                layer.properties[key] = int(float(value))
            except (TypeError, ValueError):
                layer.properties[key] = value
        elif ftype == "boolean":
            layer.properties[key] = str(value).lower() in ("1", "true", "yes", "y", "on")
        else:
            layer.properties[key] = value
        if key not in layer.prop_types:
            layer.prop_types[key] = ftype

    def on_exit(self, event):
        self.Close(True)


if __name__ == '__main__':
    app = wx.App(False)
    frame = MainFrame()
    app.MainLoop()
