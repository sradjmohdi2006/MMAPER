import os

try:
    import rasterio
    os.environ["PROJ_LIB"] = os.path.join(os.path.dirname(rasterio.__file__), "proj_data")
    os.environ["GDAL_DATA"] = os.path.join(os.path.dirname(rasterio.__file__), "gdal_data")
except Exception:
    try:
        import pyproj
        os.environ["PROJ_LIB"] = pyproj.datadir.get_data_dir()
    except Exception:
        pass

import base64
import copy
import heapq
import json
import math
import zlib

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.coords import BoundingBox
from rasterio.transform import from_origin
from shapely.geometry import Polygon, box, shape


FIELD_TEMPLATES = {
    "Blank": {},
    "Demographics": {
        "name": "text",
        "population": "number",
        "population_density": "number",
        "households": "number",
        "median_age": "number",
    },
    "Economy": {
        "name": "text",
        "gdp": "number",
        "gdp_per_capita": "number",
        "main_industry": "text",
        "unemployment_rate": "number",
    },
    "Terrain & Nature": {
        "name": "text",
        "terrain": "text",
        "elevation": "number",
        "land_use": "text",
        "soil_type": "text",
        "protected_area": "boolean",
    },
    "Infrastructure": {
        "name": "text",
        "category": "text",
        "length": "number",
        "condition": "text",
        "year_built": "number",
    },
    "Administrative": {
        "name": "text",
        "admin_level": "text",
        "capital": "boolean",
        "area": "number",
        "region": "text",
    },
    "Health Services": {
        "name": "text",
        "facility_type": "text",
        "beds": "number",
        "staff": "number",
        "opening_year": "number",
        "has_emergency": "boolean",
    },
    "Education": {
        "name": "text",
        "school_type": "text",
        "students": "number",
        "teachers": "number",
        "grades": "text",
        "has_boarding": "boolean",
    },
    "Transport Network": {
        "name": "text",
        "road_type": "text",
        "surface": "text",
        "lanes": "number",
        "speed_limit": "number",
        "toll": "boolean",
        "year_built": "number",
    },
    "Utilities & Energy": {
        "name": "text",
        "utility_type": "text",
        "capacity": "number",
        "grid_status": "text",
        "primary_fuel": "text",
        "operational": "boolean",
        "installed_year": "number",
    },
    "Water Resources": {
        "name": "text",
        "water_type": "text",
        "flow_rate": "number",
        "storage_capacity": "number",
        "quality_index": "number",
        "potable": "boolean",
    },
    "Emergency Services": {
        "name": "text",
        "service_type": "text",
        "response_time": "number",
        "units": "number",
        "on_duty_staff": "number",
        "has_helipad": "boolean",
    },
    "Agriculture & Forestry": {
        "name": "text",
        "land_use": "text",
        "crop_type": "text",
        "yield": "number",
        "area_ha": "number",
        "irrigated": "boolean",
        "owner": "text",
    },
    "Environment & Conservation": {
        "name": "text",
        "habitat_type": "text",
        "conservation_status": "text",
        "species_count": "number",
        "managed_by": "text",
        "protected": "boolean",
    },
    "Mining & Resources": {
        "name": "text",
        "mineral_type": "text",
        "reserve_estimate": "number",
        "extraction_rate": "number",
        "permit_number": "text",
        "active": "boolean",
    },
    "Urban Development": {
        "name": "text",
        "development_type": "text",
        "units": "number",
        "density": "number",
        "completion_year": "number",
        "approved": "boolean",
    },
    "Climate & Weather": {
        "name": "text",
        "station_type": "text",
        "annual_rainfall": "number",
        "mean_temp": "number",
        "elevation": "number",
        "active": "boolean",
    },
    "Security & Defence": {
        "name": "text",
        "facility_type": "text",
        "personnel": "number",
        "threat_level": "text",
        "restricted": "boolean",
    },
    "Trade & Commerce": {
        "name": "text",
        "business_type": "text",
        "employees": "number",
        "revenue": "number",
        "tax_zone": "text",
        "open": "boolean",
    },
    "Telecommunications": {
        "name": "text",
        "network_type": "text",
        "coverage_pct": "number",
        "bandwidth": "number",
        "towers": "number",
        "active": "boolean",
    },
    "Housing & Real Estate": {
        "name": "text",
        "housing_type": "text",
        "units": "number",
        "occupancy_rate": "number",
        "avg_price": "number",
        "social_housing": "boolean",
    },
    "Heritage & Culture": {
        "name": "text",
        "heritage_type": "text",
        "heritage_grade": "text",
        "visitor_count": "number",
        "listed": "boolean",
        "steward": "text",
    },
    "Tourism & Recreation": {
        "name": "text",
        "attraction_type": "text",
        "entry_fee": "number",
        "visitor_capacity": "number",
        "open_season": "text",
        "guide_required": "boolean",
    },
}

# Canonical field types understood by the standardized template format.
TEMPLATE_FIELD_TYPES = ("text", "number", "integer", "boolean")

DEFAULT_STYLE = {
    "color": "#2E74B5",
    "line_width": 2.0,
    "fill_alpha": 0.5,
    "marker": "circle",
    "marker_size": 6.0,
    "colormap": "terrain",
    "style_mode": "single",
    "attribute_field": "",
    "class_count": 5,
    "render_mode": "single",
    "rgb_bands": [1, 2, 3],
}

MARKER_CHOICES = ["circle", "square", "triangle", "star", "plus", "x"]

COLORMAP_CHOICES = [
    "terrain", "viridis", "plasma", "inferno", "magma", "cividis",
    "turbo", "coolwarm", "Greys", "Blues", "Greens", "Oranges",
    "YlGnBu", "Spectral", "RdYlGn",
]

MAX_WORKING_CELLS = 32_000_000


def _decimate_for_memory(band, max_cells):
    h, w = band.shape
    if h * w <= max_cells:
        return band, 1.0
    factor = max(1, math.ceil(math.sqrt((h * w) / max_cells)))
    rows = math.ceil(h / factor)
    cols = math.ceil(w / factor)
    while rows * cols > max_cells:
        factor += 1
        rows = math.ceil(h / factor)
        cols = math.ceil(w / factor)
    return np.ascontiguousarray(band[::factor, ::factor]), float(factor)


class Layer:
    def __init__(self, name="New Layer"):
        self.name = name
        self.children = []
        self.parent = None
        self.properties = {}
        self.prop_types = {}
        self.visible = True
        self.style = dict(DEFAULT_STYLE)

    def style_with_defaults(self):
        merged = dict(DEFAULT_STYLE)
        merged.update(self.style or {})
        return merged

    def set_style(self, style):
        merged = dict(DEFAULT_STYLE)
        merged.update(style or {})
        self.style = merged

    def add_property(self, name, value=None, ftype="text"):
        self.prop_types[name] = ftype
        self.properties.setdefault(name, value)

    def remove_property(self, name):
        self.prop_types.pop(name, None)
        self.properties.pop(name, None)

    def rename_property(self, old_name, new_name, new_type=None):
        if not new_name or (new_name == old_name and new_type is None):
            return
        if new_name != old_name and new_name in self.prop_types:
            raise ValueError(f"A property named '{new_name}' already exists.")
        old_type = self.prop_types.pop(old_name, None)
        self.prop_types[new_name] = new_type or old_type or "text"
        if old_name in self.properties:
            self.properties[new_name] = self.properties.pop(old_name)

    def contains(self, other):
        if self is other:
            return True
        return any(c.contains(other) for c in self.children)

    def all_members(self):
        members = []
        for c in self.children:
            members.append(c)
            members.extend(c.all_members())
        return members

    def is_effectively_visible(self):
        if not self.visible:
            return False
        return self.parent is None or self.parent.is_effectively_visible()

    def to_dict(self):
        return {
            "kind": "Layer",
            "name": self.name,
            "visible": self.visible,
            "style": self.style,
            "properties": self.properties,
            "prop_types": self.prop_types,
            "children": [child.to_dict() for child in self.children],
        }


class Feature:
    def __init__(self, feature_data):
        self.type = feature_data.get("type", "Feature")
        self.properties = feature_data.get("properties", {})
        self.geometry = feature_data.get("geometry", {})

    def get_name(self):
        for key in ["name", "Name", "NAME", "title", "Title", "id", "ID"]:
            if key in self.properties:
                return str(self.properties[key])
        return "Unnamed Feature"

    def get_geometry_summary(self):
        geom_type = self.geometry.get("type", "Unknown Geometry")
        coordinates = self.geometry.get("coordinates", [])

        def count_vertices(coords):
            if not isinstance(coords, list):
                return 0
            if len(coords) > 0 and not isinstance(coords[0], list):
                return 1
            return sum(count_vertices(c) for c in coords)

        vertices = count_vertices(coordinates)
        return f"{geom_type} ({vertices} vertices)"

    def to_geojson(self):
        return {
            "type": self.type,
            "geometry": self.geometry,
            "properties": self.properties,
        }


class MapLayer(Layer):
    def __init__(self, name="Unnamed Layer"):
        super().__init__(name)
        self.features = []
        self.crs = None
        self.filepath = None
        self.schema = {}

    @classmethod
    def new_empty(cls, name="New Layer", crs="EPSG:4326", schema=None):
        layer = cls(name)
        layer.crs = crs
        layer.schema = dict(schema or {})
        return layer

    @classmethod
    def from_geojson(cls, filepath, name=None):
        if not name:
            name = os.path.basename(filepath)
        layer = cls(name)
        layer.filepath = filepath
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("type") == "FeatureCollection":
            for feature_data in data.get("features", []):
                layer.features.append(Feature(feature_data))
        elif data.get("type") == "Feature":
            layer.features.append(Feature(data))
        try:
            gdf = gpd.read_file(filepath)
            layer.crs = str(gdf.crs) if gdf.crs is not None else None
        except Exception:
            layer.crs = None
        layer.schema = layer.infer_schema()
        return layer

    @classmethod
    def from_vector(cls, filepath, name=None):
        if not name:
            name = os.path.basename(filepath)
        gdf = gpd.read_file(filepath)
        layer = cls(name)
        layer.filepath = filepath
        layer.crs = str(gdf.crs) if gdf.crs is not None else None
        for feat in gdf.__geo_interface__.get("features", []):
            props = {k: v for k, v in feat.get("properties", {}).items() if v is not None}
            layer.features.append(Feature({
                "type": "Feature",
                "geometry": feat.get("geometry"),
                "properties": props,
            }))
        layer.schema = layer.infer_schema()
        return layer

    def infer_schema(self):
        keys = set()
        for f in self.features:
            keys.update(k for k, v in f.properties.items() if v is not None)
        return {k: self._guess_type(k) for k in sorted(keys)}

    def _guess_type(self, key):
        for f in self.features:
            value = f.properties.get(key)
            if isinstance(value, bool):
                return "boolean"
            if isinstance(value, (int, float)):
                return "number"
            if isinstance(value, str):
                return "text"
        return "text"

    def set_crs(self, crs):
        from pyproj import CRS
        parsed = CRS.from_user_input(crs)
        self.crs = str(parsed)

    def add_field(self, field_name, field_type="text", default=None):
        self.schema[field_name] = field_type
        for f in self.features:
            if field_name not in f.properties:
                f.properties[field_name] = default

    def remove_field(self, field_name):
        self.schema.pop(field_name, None)
        for f in self.features:
            f.properties.pop(field_name, None)

    def rename_field(self, old_name, new_name, new_type=None):
        if not new_name or (new_name == old_name and new_type is None):
            return
        if new_name != old_name and new_name in self.schema:
            raise ValueError(f"A field named '{new_name}' already exists.")
        if old_name in self.schema:
            old_type = self.schema.pop(old_name)
            self.schema[new_name] = new_type or old_type
        else:
            self.schema[new_name] = new_type or "text"
        for f in self.features:
            if old_name in f.properties:
                f.properties[new_name] = f.properties.pop(old_name)

    def add_feature(self, name, geom_type, coordinates, properties=None):
        feature = Feature({
            "type": "Feature",
            "geometry": {"type": geom_type, "coordinates": coordinates},
            "properties": properties if properties is not None else {},
        })
        feature.properties.setdefault("name", name)
        self.features.append(feature)
        return feature

    def remove_feature(self, feature):
        self.features = [f for f in self.features if f is not feature]

    def duplicate_feature(self, feature):
        new_feature = copy.deepcopy(feature)
        self.features.append(new_feature)
        return new_feature

    def save_to_geojson(self, filepath):
        data = {
            "type": "FeatureCollection",
            "features": [f.to_geojson() for f in self.features],
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        self.filepath = filepath

    def to_dict(self):
        return {
            "kind": "MapLayer",
            "name": self.name,
            "crs": self.crs,
            "filepath": self.filepath,
            "schema": self.schema,
            "visible": self.visible,
            "style": self.style,
            "properties": self.properties,
            "prop_types": self.prop_types,
            "features": [f.to_geojson() for f in self.features],
            "children": [child.to_dict() for child in self.children],
        }

    @classmethod
    def from_dict(cls, data):
        layer = cls(data.get("name", "Unnamed Layer"))
        layer.crs = data.get("crs")
        layer.filepath = data.get("filepath")
        layer.schema = dict(data.get("schema", {}) or {})
        layer.visible = bool(data.get("visible", True))
        layer.style = dict(DEFAULT_STYLE)
        layer.style.update(data.get("style", {}) or {})
        layer.properties = dict(data.get("properties", {}) or {})
        layer.prop_types = dict(data.get("prop_types", {}) or {})
        layer.features = [Feature(f) for f in data.get("features", [])]
        for child_data in data.get("children", []):
            child = _layer_from_dict(child_data)
            child.parent = layer
            layer.children.append(child)
        return layer

    def to_dataframe(self):
        rows = []
        for f in self.features:
            row = dict(f.properties)
            row["_geometry"] = f.get_geometry_summary()
            rows.append(row)
        return pd.DataFrame(rows)

    def to_geodataframe(self):
        geoms = []
        for f in self.features:
            try:
                geoms.append(shape(f.geometry) if f.geometry else None)
            except Exception:
                geoms.append(None)
        gdf = gpd.GeoDataFrame(geometry=geoms)
        if self.crs:
            gdf.set_crs(self.crs, inplace=True)
        return gdf

    def reproject(self, target_crs):
        if not self.crs:
            raise ValueError("Layer has no CRS. Set a CRS first.")
        gdf = self.to_geodataframe().to_crs(target_crs)
        self.crs = str(gdf.crs)
        for f, geom in zip(self.features, gdf.geometry):
            if geom is not None and not geom.is_empty:
                f.geometry = geom.__geo_interface__

    def buffer_feature(self, feature, distance):
        geom = shape(feature.geometry).buffer(distance)
        feature.geometry = geom.__geo_interface__

    def simplify_feature(self, feature, tolerance):
        geom = shape(feature.geometry).simplify(tolerance)
        feature.geometry = geom.__geo_interface__

    def add_geometry_fields(self, area_field="area", length_field="length"):
        for f in self.features:
            try:
                geom = shape(f.geometry)
                if not geom.is_empty:
                    if area_field:
                        f.properties[area_field] = round(abs(geom.area), 6)
                    if length_field:
                        f.properties[length_field] = round(geom.length, 6)
            except Exception:
                continue
        for key in (area_field, length_field):
            if key and key not in self.schema:
                self.schema[key] = "number"

    def add_centroid_field(self, field_name="centroid"):
        for f in self.features:
            try:
                geom = shape(f.geometry)
                if not geom.is_empty:
                    centroid = geom.centroid
                    f.properties[field_name] = [round(centroid.x, 6), round(centroid.y, 6)]
            except Exception:
                continue
        if field_name not in self.schema:
            self.schema[field_name] = "text"

    def coordinate_properties(self):
        candidates = []
        for f in self.features:
            for key, value in f.properties.items():
                if (isinstance(value, (list, tuple)) and len(value) >= 2 and
                        all(isinstance(v, (int, float)) for v in value[:2])):
                    if key not in candidates:
                        candidates.append(key)
        return candidates

    def split_geometry_coordinates(self, feature, x_field="x", y_field="y"):
        try:
            geom = shape(feature.geometry)
        except Exception:
            return False
        if geom.is_empty:
            return False
        point = geom.centroid
        feature.properties[x_field] = round(point.x, 6)
        feature.properties[y_field] = round(point.y, 6)
        for key in (x_field, y_field):
            if key not in self.schema:
                self.schema[key] = "number"
        return True

    def split_property_coordinates(self, feature, source_key, x_field="x", y_field="y"):
        value = feature.properties.get(source_key)
        if not (isinstance(value, (list, tuple)) and len(value) >= 2):
            return False
        try:
            x, y = float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return False
        feature.properties[x_field] = round(x, 6)
        feature.properties[y_field] = round(y, 6)
        for key in (x_field, y_field):
            if key not in self.schema:
                self.schema[key] = "number"
        return True

    def get_attribute_summary(self):
        summary = {"Feature Count": len(self.features)}
        df = self.to_dataframe()
        for col in df.columns:
            if col == "_geometry":
                continue
            values = pd.to_numeric(df[col], errors="coerce")
            if values.notna().any():
                summary[f"Total {col}"] = round(float(values.sum()), 2)
                summary[f"Average {col}"] = round(float(values.mean()), 2)
        return summary

    def compute_area_sq_units(self):
        total = 0.0
        for f in self.features:
            try:
                geom = shape(f.geometry)
                if geom.is_empty:
                    continue
                total += abs(geom.area)
            except Exception:
                continue
        return total

    def get_geographic_summary(self):
        total_area = self.compute_area_sq_units()
        summary = {"Total Area (sq units)": round(total_area, 4)}
        population = None
        for f in self.features:
            for key, value in f.properties.items():
                if key.lower() in ("population", "pop", "residents"):
                    try:
                        population = float(value)
                    except (TypeError, ValueError):
                        population = None
                    break
            if population is not None:
                break
        if population is not None and total_area > 0:
            summary["Population Density"] = round(population / total_area, 4)
        return summary


class TerrainLayer(Layer):
    def __init__(self, filepath, name=None):
        super().__init__(name if name else os.path.basename(filepath))
        self.filepath = filepath
        self.crs = None
        self.bounds = None
        self.resolution = None
        self.transform = None
        self.width = None
        self.height = None
        self.data = None
        self.bands = None
        self.band_count = 1
        self.active_band = 1
        self.band_names = []
        self.stats = {}
        self.original_shape = None
        self.reduced = False
        self._load()

    def _load(self):
        with rasterio.open(self.filepath) as src:
            self.bounds = src.bounds
            self.resolution = src.res
            self.transform = src.transform
            self.crs = str(src.crs) if src.crs is not None else None
            count = src.count
            nodata = src.nodata
            factor = 1.0
            bands = []
            for i in range(1, count + 1):
                band = src.read(i).astype("float64")
                if nodata is not None:
                    band[band == nodata] = np.nan
                if band.size > MAX_WORKING_CELLS:
                    band, factor = _decimate_for_memory(band, MAX_WORKING_CELLS)
                bands.append(band)
            self.bands = bands
            self.band_count = len(bands) or 1
            self.active_band = 1
            self.band_names = ["Band %d" % (i + 1) for i in range(self.band_count)]
            self.data = self.bands[0] if self.bands else None
            self.height, self.width = self.data.shape
            self.original_shape = (src.height, src.width)
            self.reduced = self.original_shape != (self.height, self.width)
            if self.reduced:
                new_res = (self.resolution[0] * factor, self.resolution[1] * factor)
                self.resolution = new_res
                self.transform = from_origin(self.bounds.left, self.bounds.top,
                                             new_res[0], new_res[1])
        self.recompute_stats()

    def set_active_band(self, band_index):
        if not self.bands or not (1 <= int(band_index) <= len(self.bands)):
            return False
        self.active_band = int(band_index)
        self.data = self.bands[self.active_band - 1]
        self.recompute_stats()
        return True

    def _ensure_band_names(self):
        count = self.band_count
        if len(self.band_names) > count:
            del self.band_names[count:]
        while len(self.band_names) < count:
            self.band_names.append("Band %d" % (len(self.band_names) + 1))

    def add_band(self, value=0.0, name=None):
        if self.data is None:
            return False
        if not self.bands:
            self.bands = []
        self.bands.append(np.full(self.data.shape, float(value), dtype="float64"))
        self.band_count = len(self.bands)
        self._ensure_band_names()
        if name and name.strip():
            self.band_names[-1] = name.strip()
        return True

    def duplicate_band(self, band_index=None):
        if not self.bands:
            return False
        if band_index is None:
            band_index = self.active_band
        band_index = int(band_index)
        if not (1 <= band_index <= len(self.bands)):
            return False
        self.bands.append(np.array(self.bands[band_index - 1], dtype="float64"))
        self.band_count = len(self.bands)
        self._ensure_band_names()
        self.band_names[-1] = self.band_names[band_index - 1] + " Copy"
        return True

    def remove_band(self, band_index=None):
        if not self.bands or len(self.bands) <= 1:
            return False
        if band_index is None:
            band_index = self.active_band
        band_index = int(band_index)
        if not (1 <= band_index <= len(self.bands)):
            return False
        del self.bands[band_index - 1]
        if self.band_names and 1 <= band_index <= len(self.band_names):
            del self.band_names[band_index - 1]
        self.band_count = len(self.bands)
        self._ensure_band_names()
        if self.active_band > self.band_count:
            self.active_band = self.band_count
        self.data = self.bands[self.active_band - 1]
        self.recompute_stats()
        return True

    def rename_band(self, band_index, name):
        if not self.bands:
            return False
        band_index = int(band_index)
        if not (1 <= band_index <= len(self.bands)):
            return False
        self._ensure_band_names()
        name = (name or "").strip() or ("Band %d" % band_index)
        self.band_names[band_index - 1] = name
        return True

    def band_stats(self, band_index=None):
        if band_index is None:
            band_index = self.active_band
        band_index = int(band_index)
        if not self.bands or not (1 <= band_index <= len(self.bands)):
            return {}
        band = self.bands[band_index - 1]
        stats = {}
        if band is not None and band.size and np.isfinite(band).any():
            stats["Minimum Elevation"] = round(float(np.nanmin(band)), 2)
            stats["Maximum Elevation"] = round(float(np.nanmax(band)), 2)
            stats["Mean Elevation"] = round(float(np.nanmean(band)), 2)
            stats["Std Deviation"] = round(float(np.nanstd(band)), 2)
            stats["Elevation Range"] = round(float(np.nanmax(band) - np.nanmin(band)), 2)
        return stats

    def rgb_array(self, r_band=None, g_band=None, b_band=None):
        if not self.bands:
            return None
        picks = [r_band, g_band, b_band]
        for i, p in enumerate(picks):
            if p is None:
                picks[i] = min(i + 1, len(self.bands))
        out = []
        for p in picks:
            band = np.asarray(self.bands[int(p) - 1], dtype="float64")
            band = np.nan_to_num(band, nan=0.0)
            finite = band[np.isfinite(band)]
            if finite.size == 0:
                out.append(np.zeros_like(band))
            else:
                lo, hi = np.percentile(finite, [2.0, 98.0])
                if hi > lo:
                    out.append(np.clip((band - lo) / (hi - lo), 0.0, 1.0))
                else:
                    out.append(np.zeros_like(band))
        return np.stack(out, axis=-1).astype("float32")

    def recompute_stats(self):
        self.stats = {}
        if self.data is None or self.data.size == 0:
            return
        band = self.data
        if np.isfinite(band).any():
            self.stats["Minimum Elevation"] = round(float(np.nanmin(band)), 2)
            self.stats["Maximum Elevation"] = round(float(np.nanmax(band)), 2)
            self.stats["Mean Elevation"] = round(float(np.nanmean(band)), 2)
            self.stats["Std Deviation"] = round(float(np.nanstd(band)), 2)
            self.stats["Elevation Range"] = round(float(np.nanmax(band) - np.nanmin(band)), 2)

    @classmethod
    def new_empty(cls, name="New Terrain", crs="EPSG:4326", width=100, height=100,
                  bounds=None, resolution=None, value=0.0, bands=1):
        layer = cls.__new__(cls)
        Layer.__init__(layer, name)
        layer.filepath = None
        layer.crs = crs
        layer.width = int(width)
        layer.height = int(height)
        layer.band_count = max(1, int(bands))
        layer.bands = [np.full((layer.height, layer.width), float(value), dtype="float64")
                       for _ in range(layer.band_count)]
        layer.active_band = 1
        layer.data = layer.bands[0]
        layer.band_names = ["Band %d" % (i + 1) for i in range(layer.band_count)]
        layer.bounds = None
        layer.resolution = None
        layer.transform = None
        layer.stats = {}
        layer.original_shape = (layer.height, layer.width)
        layer.reduced = False
        layer._set_geometry(bounds=bounds, resolution=resolution)
        layer.recompute_stats()
        return layer

    def _set_geometry(self, bounds=None, resolution=None):
        w, h = self.width, self.height
        if bounds is not None and resolution is None:
            left, bottom, right, top = (float(bounds[0]), float(bounds[1]),
                                        float(bounds[2]), float(bounds[3]))
            self.bounds = BoundingBox(left, bottom, right, top)
            self.resolution = ((right - left) / w, (top - bottom) / h)
        elif resolution is not None and bounds is None:
            rx, ry = (float(resolution[0]), float(resolution[1]))
            self.resolution = (rx, ry)
            self.bounds = BoundingBox(0.0, 0.0, rx * w, ry * h)
        else:
            self.resolution = (1.0, 1.0)
            self.bounds = BoundingBox(0.0, 0.0, float(w), float(h))
        self.transform = from_origin(self.bounds.left, self.bounds.top,
                                     self.resolution[0], self.resolution[1])

    def save_to_geotiff(self, filepath):
        bands = self.bands or ([self.data] if self.data is not None else [])
        profile = {
            "driver": "GTiff",
            "height": self.data.shape[0],
            "width": self.data.shape[1],
            "count": len(bands),
            "dtype": "float64",
            "crs": self.crs or "EPSG:4326",
            "transform": self.transform,
        }
        with rasterio.open(filepath, "w", **profile) as dst:
            for i, band in enumerate(bands, start=1):
                dst.write(band, i)
        self.filepath = filepath

    def fill(self, value):
        self.data[...] = float(value)
        self.recompute_stats()

    def fill_rect(self, row_start, row_end, col_start, col_end, value):
        h, w = self.data.shape
        r0 = max(0, int(row_start))
        r1 = min(h, int(row_end))
        c0 = max(0, int(col_start))
        c1 = min(w, int(col_end))
        if r0 < r1 and c0 < c1:
            self.data[r0:r1, c0:c1] = float(value)
        self.recompute_stats()

    def add_constant(self, value):
        self.data += float(value)
        self.recompute_stats()

    def multiply_constant(self, factor):
        self.data *= float(factor)
        self.recompute_stats()

    def invert(self):
        finite = self.data[np.isfinite(self.data)]
        if finite.size == 0:
            return
        vmin = float(np.nanmin(finite))
        vmax = float(np.nanmax(finite))
        self.data[...] = vmin + vmax - self.data
        self.recompute_stats()

    def add_gradient(self, direction, start_value, end_value):
        h, w = self.data.shape
        row_t = np.linspace(0.0, 1.0, h).reshape(-1, 1)
        col_t = np.linspace(0.0, 1.0, w).reshape(1, -1)
        direction = direction or "North->South"
        if direction == "South->North":
            t = 1.0 - row_t
        elif direction == "West->East":
            t = col_t
        elif direction == "East->West":
            t = 1.0 - col_t
        elif direction == "NW->SE":
            t = (row_t + col_t) / 2.0
        elif direction == "NE->SW":
            t = (row_t + (1.0 - col_t)) / 2.0
        else:
            t = row_t
        delta = float(end_value) - float(start_value)
        self.data += (float(start_value) + t * delta)
        self.recompute_stats()

    def add_noise(self, amount):
        amount = float(amount)
        if amount:
            rng = np.random.default_rng()
            self.data += rng.normal(0.0, abs(amount), self.data.shape)
        self.recompute_stats()

    def smooth(self, passes=1):
        passes = max(1, int(passes))
        kernel = np.ones((3, 3)) / 9.0
        from scipy.ndimage import convolve
        current = self.data
        for _ in range(passes):
            current = convolve(current, kernel, mode="nearest")
        self.data[...] = current
        self.recompute_stats()

    def sample_elevation(self, longitude, latitude):
        if self.data is None or self.transform is None:
            return None
        try:
            row, col = rasterio.transform.rowcol(self.transform, longitude, latitude)
            h, w = self.data.shape
            if 0 <= row < h and 0 <= col < w:
                value = self.data[row, col]
                if np.isfinite(value):
                    return float(value)
        except Exception:
            return None
        return None

    def get_geometry_summary(self):
        left, bottom, right, top = (self.bounds.left, self.bounds.bottom,
                                    self.bounds.right, self.bounds.top)
        return f"Bounds ({left:.4f}, {bottom:.4f}) to ({right:.4f}, {top:.4f})"

    def to_dict(self):
        return {
            "kind": "TerrainLayer",
            "name": self.name,
            "filepath": self.filepath,
            "crs": self.crs,
            "bounds": list(self.bounds) if self.bounds else None,
            "resolution": list(self.resolution) if self.resolution else None,
            "stats": self.stats,
            "bands": [_encode_band(b) for b in (self.bands or [])],
            "band_names": list(self.band_names or []),
            "active_band": self.active_band,
            "original_shape": list(self.original_shape) if self.original_shape else None,
            "reduced": bool(self.reduced),
            "visible": self.visible,
            "style": self.style,
            "properties": self.properties,
            "prop_types": self.prop_types,
            "children": [child.to_dict() for child in self.children],
        }

    @classmethod
    def from_dict(cls, data):
        filepath = data.get("filepath")
        bands_data = data.get("bands")
        data_arr = data.get("data")
        if bands_data:
            layer = cls.__new__(cls)
            Layer.__init__(layer, data.get("name") or
                           (os.path.basename(filepath) if filepath else "Terrain Layer"))
            layer.filepath = filepath
            layer.crs = data.get("crs")
            bounds = data.get("bounds")
            if bounds:
                layer.bounds = BoundingBox(bounds[0], bounds[1], bounds[2], bounds[3])
            else:
                layer.bounds = None
            resolution = data.get("resolution")
            layer.resolution = tuple(resolution) if resolution else None
            layer.bands = [_decode_band(b) for b in bands_data]
            layer.band_count = len(layer.bands) or 1
            layer.active_band = int(data.get("active_band", 1) or 1)
            if not (1 <= layer.active_band <= layer.band_count):
                layer.active_band = 1
            layer.data = layer.bands[layer.active_band - 1]
            layer.height, layer.width = layer.data.shape
            if layer.bounds is not None and layer.resolution is not None:
                layer.transform = from_origin(layer.bounds.left, layer.bounds.top,
                                              layer.resolution[0], layer.resolution[1])
            else:
                layer.transform = None
            layer.stats = dict(data.get("stats", {}) or {})
            layer.recompute_stats()
        elif data_arr is not None:
            layer = cls.__new__(cls)
            Layer.__init__(layer, data.get("name") or
                           (os.path.basename(filepath) if filepath else "Terrain Layer"))
            layer.filepath = filepath
            layer.crs = data.get("crs")
            bounds = data.get("bounds")
            if bounds:
                layer.bounds = BoundingBox(bounds[0], bounds[1], bounds[2], bounds[3])
            else:
                layer.bounds = None
            resolution = data.get("resolution")
            layer.resolution = tuple(resolution) if resolution else None
            layer.bands = [np.array(data_arr, dtype="float64")]
            layer.band_count = 1
            layer.active_band = 1
            layer.data = layer.bands[0]
            layer.height, layer.width = layer.data.shape
            if layer.bounds is not None and layer.resolution is not None:
                layer.transform = from_origin(layer.bounds.left, layer.bounds.top,
                                              layer.resolution[0], layer.resolution[1])
            else:
                layer.transform = None
            layer.stats = dict(data.get("stats", {}) or {})
            layer.recompute_stats()
        elif filepath and os.path.exists(filepath):
            layer = cls(filepath, name=data.get("name"))
        else:
            layer = cls.__new__(cls)
            Layer.__init__(layer, data.get("name") or
                           (os.path.basename(filepath) if filepath else "Terrain Layer"))
            layer.filepath = filepath
            layer.crs = data.get("crs")
            bounds = data.get("bounds")
            if bounds:
                layer.bounds = BoundingBox(bounds[0], bounds[1], bounds[2], bounds[3])
            else:
                layer.bounds = None
            resolution = data.get("resolution")
            layer.resolution = tuple(resolution) if resolution else None
            layer.transform = None
            layer.width = None
            layer.height = None
            layer.data = None
            layer.bands = None
            layer.band_count = 1
            layer.active_band = 1
            layer.stats = dict(data.get("stats", {}) or {})
        layer.band_names = list(data.get("band_names") or [])
        if len(layer.band_names) != getattr(layer, "band_count", 1):
            layer.band_names = ["Band %d" % (i + 1)
                                for i in range(getattr(layer, "band_count", 1))]
        layer.properties = dict(data.get("properties", {}) or {})
        layer.prop_types = dict(data.get("prop_types", {}) or {})
        layer.visible = bool(data.get("visible", True))
        layer.style = dict(DEFAULT_STYLE)
        layer.style.update(data.get("style", {}) or {})
        if getattr(layer, "original_shape", None) is None:
            orig_shape = data.get("original_shape")
            if orig_shape:
                layer.original_shape = tuple(orig_shape)
            elif layer.data is not None:
                layer.original_shape = tuple(layer.data.shape)
            else:
                layer.original_shape = None
        layer.reduced = bool(data.get("reduced", getattr(layer, "reduced", False)))
        for child_data in data.get("children", []):
            child = _layer_from_dict(child_data)
            child.parent = layer
            layer.children.append(child)
        return layer


# ---------------------------------------------------------------------------
# Spatial Analysis Engine
# ---------------------------------------------------------------------------

def _haversine_km(lon1, lat1, lon2, lat2):
    """Great-circle distance in kilometres between two lon/lat points."""
    radius = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2.0) ** 2 +
         math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2)
    return 2.0 * radius * math.asin(min(1.0, math.sqrt(a)))


def _to_lonlat_geoms(layer):
    """Return a layer's geometries as a GeoSeries reprojected to WGS84."""
    if not isinstance(layer, MapLayer) or not layer.features:
        return gpd.GeoSeries([], crs="EPSG:4326")
    gdf = layer.to_geodataframe()
    try:
        if gdf.crs is not None and str(gdf.crs) != "EPSG:4326":
            gdf = gdf.to_crs("EPSG:4326")
    except Exception:
        pass
    return gdf.geometry


def _point_xy(geom):
    if geom is None or getattr(geom, "is_empty", True):
        return None
    if geom.geom_type == "Point":
        return (float(geom.x), float(geom.y))
    centroid = geom.centroid
    return (float(centroid.x), float(centroid.y))


def _iter_line_coords(geom):
    if geom is None or getattr(geom, "is_empty", True):
        return
    if geom.geom_type == "LineString":
        yield list(geom.coords)
    elif geom.geom_type == "MultiLineString":
        for line in geom.geoms:
            yield list(line.coords)
    elif geom.geom_type == "Polygon":
        yield list(geom.exterior.coords)
    elif geom.geom_type == "MultiPolygon":
        for poly in geom.geoms:
            yield list(poly.exterior.coords)


def _point_line_min_km(lon, lat, line_geom):
    """Minimum great-circle distance (km) from a point to a line's vertices."""
    best = float("inf")
    for coords in _iter_line_coords(line_geom):
        for x, y in coords:
            try:
                best = min(best, _haversine_km(lon, lat, float(x), float(y)))
            except (TypeError, ValueError):
                continue
    return best


def compute_spatial_suitability(resource_points_layer, transport_lines_layer,
                                max_distance_km=50.0):
    """Score each deposit point by its distance to the nearest transport corridor.

    Returns a list of 0..1 scores, one per feature in resource_points_layer
    (1.0 = right on a corridor, 0.0 = at or beyond max_distance_km).
    """
    if not resource_points_layer.features or not transport_lines_layer.features:
        return []
    pt_geoms = _to_lonlat_geoms(resource_points_layer)
    line_geoms = _to_lonlat_geoms(transport_lines_layer)
    scores = []
    for i, geom in enumerate(pt_geoms):
        pt = _point_xy(geom)
        if pt is None:
            scores.append(0.0)
            continue
        min_dist = min((_point_line_min_km(pt[0], pt[1], line)
                        for line in line_geoms if line is not None),
                       default=float("inf"))
        scores.append(max(0.0, 1.0 - (min_dist / max_distance_km)))
    return scores


def _geodetic_ring_polygon(lon, lat, radius_km, segments=48):
    """Approximate a circle of radius_km around (lon, lat) as a polygon."""
    dlat = radius_km / 111.32
    cos_lat = math.cos(math.radians(lat))
    dlon = radius_km / (111.32 * cos_lat) if cos_lat > 0.05 else radius_km / 5.0
    points = []
    for t in range(segments):
        ang = 2.0 * math.pi * t / segments
        points.append((lon + dlon * math.cos(ang), lat + dlat * math.sin(ang)))
    return Polygon(points)


def _approx_polygon_area_km2(poly):
    """Approximate polygon area in km^2 from lon/lat (cos-latitude scaling)."""
    if poly is None or poly.is_empty:
        return 0.0
    lon, lat = poly.exterior.xy
    lat_mid = (min(lat) + max(lat)) / 2.0
    sx = 111.32 * math.cos(math.radians(lat_mid))
    sy = 111.32
    xs = [x * sx for x in lon]
    ys = [y * sy for y in lat]
    area = 0.0
    for i in range(len(xs) - 1):
        area += xs[i] * ys[i + 1] - xs[i + 1] * ys[i]
    return abs(area) / 2.0


def _voronoi_polygons(points_lonlat):
    """Thiessen cells around lon/lat points, clipped to the data bbox."""
    from scipy.spatial import Voronoi
    arr = np.asarray(points_lonlat, dtype=float)
    if len(arr) < 2:
        return [None] * len(arr)
    xmin, ymin = arr[:, 0].min(), arr[:, 1].min()
    xmax, ymax = arr[:, 0].max(), arr[:, 1].max()
    span_x = (xmax - xmin) or 1.0
    span_y = (ymax - ymin) or 1.0
    pad_x, pad_y = 0.1 * span_x, 0.1 * span_y
    clip = box(xmin - pad_x, ymin - pad_y, xmax + pad_x, ymax + pad_y)
    frame = np.array([[xmin - 4 * span_x, ymin - 4 * span_y],
                      [xmin - 4 * span_x, ymax + 4 * span_y],
                      [xmax + 4 * span_x, ymax + 4 * span_y],
                      [xmax + 4 * span_x, ymin - 4 * span_y]])
    vor = Voronoi(np.vstack([arr, frame]))
    cells = []
    for i in range(len(arr)):
        region = vor.regions[vor.point_region[i]]
        if not region or -1 in region:
            cells.append(None)
            continue
        poly = Polygon([vor.vertices[v] for v in region])
        clipped = poly.intersection(clip)
        if clipped.is_empty:
            cells.append(None)
            continue
        if clipped.geom_type == "MultiPolygon":
            clipped = max(clipped.geoms, key=lambda p: p.area)
        cells.append(clipped if clipped.geom_type == "Polygon" else None)
    return cells


def _buffer_rings_layer(org_layer, rings_km):
    """Polygon layer of multi-ring proximity buffers around each org point."""
    layer = MapLayer.new_empty("Org Influence Buffers", crs="EPSG:4326",
                               schema={"name": "text", "org": "text",
                                       "ring_km": "number"})
    geoms = _to_lonlat_geoms(org_layer)
    rings = sorted(float(r) for r in (rings_km or ()))
    for i, feature in enumerate(org_layer.features):
        pt = _point_xy(geoms.iloc[i] if i < len(geoms) else None)
        if pt is None:
            continue
        name = feature.get_name()
        for ring in rings:
            poly = _geodetic_ring_polygon(pt[0], pt[1], ring)
            layer.add_feature(f"{name} {ring:g}km ring", "Polygon",
                              poly.__geo_interface__["coordinates"],
                              {"org": name, "ring_km": ring})
    return layer


def _voronoi_layer(org_layer):
    """Polygon layer of Thiessen cells for the org points."""
    geoms = _to_lonlat_geoms(org_layer)
    pts = [_point_xy(g) for g in geoms]
    valid = [i for i, p in enumerate(pts) if p is not None]
    cells = _voronoi_polygons([pts[i] for i in valid]) if len(valid) >= 2 else []
    layer = MapLayer.new_empty("Org Voronoi Cells", crs="EPSG:4326",
                               schema={"name": "text", "org": "text",
                                       "area_km2": "number"})
    for idx, cell in zip(valid, cells):
        if cell is None:
            continue
        name = org_layer.features[idx].get_name()
        layer.add_feature(f"{name} cell", "Polygon",
                          cell.__geo_interface__["coordinates"],
                          {"org": name, "area_km2": round(_approx_polygon_area_km2(cell), 2)})
    return layer


def _density_grid(layer, cells=48):
    """2D density grid (heatmap) of org point clusters over the data bbox."""
    geoms = _to_lonlat_geoms(layer)
    pts = [p for p in (_point_xy(g) for g in geoms) if p is not None]
    if not pts:
        return None, None
    xs = np.array([p[0] for p in pts])
    ys = np.array([p[1] for p in pts])
    hist, xedges, yedges = np.histogram2d(xs, ys, bins=int(cells))
    extent = (float(xedges[0]), float(xedges[-1]),
              float(yedges[0]), float(yedges[-1]))
    return np.flipud(hist.T), extent


class SpatialAnalysisEngine:
    """Vector + raster multi-criteria analysis for resource and org layers."""

    _DIJKSTRA_CELL_LIMIT = 4_000_000

    def _sample_at(self, layer, array, lon, lat):
        if array is None or layer.transform is None:
            return None
        try:
            row, col = rasterio.transform.rowcol(layer.transform, lon, lat)
            h, w = array.shape
            if 0 <= row < h and 0 <= col < w:
                value = array[row, col]
                if np.isfinite(value):
                    return float(value)
        except Exception:
            return None
        return None

    def _terrain_slope_pct(self, layer):
        if layer.data is None:
            return None
        res_x, res_y = layer.resolution if layer.resolution else (1.0, 1.0)
        res_x = res_x or 1.0
        res_y = res_y or 1.0
        with np.errstate(divide="ignore", invalid="ignore"):
            gy, gx = np.gradient(layer.data)
            slope = np.sqrt((gx / res_x) ** 2 + (gy / res_y) ** 2) * 100.0
        return slope

    @staticmethod
    def _attach_fields(layer, field_types):
        for key, ftype in field_types.items():
            layer.add_field(key, ftype)

    @staticmethod
    def _classify_zone(km, rings):
        if km is None:
            return "Zone 4 (isolated)"
        for i, r in enumerate(rings, start=1):
            if km < r:
                return f"Zone {i} (< {r:g} km)"
        return f"Zone {len(rings) + 1} (isolated)"

    def compute_resource_suitability(self, deposits_layer, transport_layers=None,
                                     terrain_layer=None, max_distance_km=50.0,
                                     max_slope_pct=None, min_elevation=None,
                                     max_elevation=None, cost_layer=None,
                                     cost_band=None, cost_weight=0.0,
                                     diagonal=True, max_reach_cost=None):
        """Multi-criteria overlay scoring for extraction / infrastructure siting.

        Combines distance-to-infrastructure proximity with optional terrain
        slope / elevation exclusions and attaches calculated fields to every
        deposit feature. Returns a summary dict.

        When a cost surface (TerrainLayer) is supplied together with transport
        corridors, each deposit is additionally scored by its cost-weighted
        network reach: a single multi-source Dijkstra run radiates from every
        corridor cell and each deposit reads the accumulated traversal cost at
        its own cell. cost_weight (0..1) blends the proximity score with the
        normalized reach score (1.0 = only proximity, 0.0 = only network
        reach). Reach costs are normalized by max_reach_cost when given, or by
        the largest finite reach cost seen in the run otherwise. The optional
        max_distance_km still governs the proximity component.
        """
        empty = {"analyzed": 0, "excluded": 0, "best": None, "best_score": 0.0,
                 "min_score": 0.0, "max_score": 0.0, "mean_score": 0.0}
        if not isinstance(deposits_layer, MapLayer) or not deposits_layer.features:
            return empty
        transport_layers = [l for l in (transport_layers or [])
                            if isinstance(l, MapLayer) and l.features]
        has_terrain = isinstance(terrain_layer, TerrainLayer)
        slope = self._terrain_slope_pct(terrain_layer) if has_terrain else None
        deposit_geoms = _to_lonlat_geoms(deposits_layer)
        line_geoms_by_layer = [_to_lonlat_geoms(l) for l in transport_layers]

        # -- optional cost-weighted network reach --------------------------
        cost = self._cost_array(cost_layer, cost_band)
        use_cost = (cost is not None and line_geoms_by_layer
                    and cost.size <= self._DIJKSTRA_CELL_LIMIT)
        weight = 0.0
        if use_cost:
            try:
                weight = max(0.0, min(1.0, float(cost_weight)))
            except (TypeError, ValueError):
                weight = 0.0
        reach_dist = None
        if use_cost:
            source_cells = self._corridor_source_cells(cost_layer,
                                                       line_geoms_by_layer)
            if source_cells:
                reach_dist, _ = self._dijkstra_from(cost, sorted(source_cells),
                                                    diagonal)

        field_types = {"suitability_score": "number",
                       "distance_to_infra_km": "number",
                       "suitability_excluded": "boolean"}
        if has_terrain:
            field_types["elevation_m"] = "number"
            field_types["slope_pct"] = "number"
        if use_cost:
            field_types["network_reach_cost"] = "number"
            field_types["network_reach_score"] = "number"
        self._attach_fields(deposits_layer, field_types)

        analyzed = 0
        excluded = 0
        scores = []
        best_feature = None
        best_score = -1.0
        # Pre-compute every deposit's reach cost so the normalizer is stable
        # across all features before any score is blended.
        pts = []
        for i in range(len(deposits_layer.features)):
            geom = deposit_geoms.iloc[i] if i < len(deposit_geoms) else None
            pts.append(_point_xy(geom))
        reach_costs = []
        if use_cost:
            for pt in pts:
                rc = None
                if pt is not None and reach_dist is not None:
                    cell = self._snap_to_grid(cost_layer, pt[0], pt[1])
                    if cell is not None and np.isfinite(reach_dist[cell]):
                        rc = float(reach_dist[cell])
                reach_costs.append(rc)
        finite_reach = [r for r in reach_costs if r is not None]
        norm_max = 0.0
        if use_cost:
            norm_max = (float(max_reach_cost) if (max_reach_cost or 0) > 0
                        else (max(finite_reach) if finite_reach else 0.0))
        for i, feature in enumerate(deposits_layer.features):
            pt = pts[i] if i < len(pts) else None
            reach_cost = reach_costs[i] if i < len(reach_costs) else None
            if pt is None:
                feature.properties["suitability_score"] = 0.0
                feature.properties["suitability_excluded"] = True
                if use_cost:
                    feature.properties["network_reach_cost"] = None
                    feature.properties["network_reach_score"] = 0.0
                continue
            analyzed += 1
            min_dist = float("inf")
            for line_geoms in line_geoms_by_layer:
                for line in line_geoms:
                    if line is not None:
                        min_dist = min(min_dist, _point_line_min_km(pt[0], pt[1], line))
            if min_dist == float("inf"):
                feature.properties["distance_to_infra_km"] = None
                proximity = 1.0 if not transport_layers else 0.0
            else:
                feature.properties["distance_to_infra_km"] = round(min_dist, 3)
                proximity = max(0.0, 1.0 - (min_dist / max_distance_km))
            if use_cost:
                feature.properties["network_reach_cost"] = (
                    round(reach_cost, 3) if reach_cost is not None else None)
            mask = 1.0
            if has_terrain and slope is not None:
                elevation = self._sample_at(terrain_layer, terrain_layer.data,
                                            pt[0], pt[1])
                slope_value = self._sample_at(terrain_layer, slope, pt[0], pt[1])
                feature.properties["elevation_m"] = elevation
                feature.properties["slope_pct"] = (round(slope_value, 2)
                                                   if slope_value is not None else None)
                if elevation is not None:
                    if min_elevation is not None and elevation < min_elevation:
                        mask = 0.0
                    if max_elevation is not None and elevation > max_elevation:
                        mask = 0.0
                if (slope_value is not None and max_slope_pct is not None
                        and slope_value > max_slope_pct):
                    mask = 0.0
            reach_score = 0.0
            if reach_cost is not None and norm_max > 0.0:
                reach_score = max(0.0, 1.0 - (reach_cost / norm_max))
            elif reach_cost is not None:
                reach_score = 1.0
            if use_cost:
                feature.properties["network_reach_score"] = round(reach_score, 4)
            if weight > 0.0:
                if weight >= 1.0:
                    score = reach_score * mask
                else:
                    score = (proximity * (1.0 - weight)
                             + reach_score * weight) * mask
                score = round(score, 4)
            else:
                score = round(proximity * mask, 4)
            flag = mask < 1.0
            feature.properties["suitability_score"] = score
            feature.properties["suitability_excluded"] = bool(flag)
            scores.append(score)
            if flag:
                excluded += 1
            elif score > best_score:
                best_score = score
                best_feature = feature.get_name()
        if not scores:
            scores = [0.0]
        return {
            "analyzed": analyzed,
            "excluded": excluded,
            "best": best_feature,
            "best_score": round(best_score if best_feature is not None else 0.0, 4),
            "min_score": round(min(scores), 4),
            "max_score": round(max(scores), 4),
            "mean_score": round(sum(scores) / len(scores), 4),
        }

    def generate_org_influence_zones(self, org_layer, method="buffers",
                                     buffer_rings_km=(10.0, 50.0, 100.0),
                                     density_cells=48):
        """Partition organisational influence around HQ / cell points.

        Always attaches nearest_org_km and org_control_zone fields. Depending on
        the method it also produces an optional polygons_layer ('buffers' or
        'voronoi') or a density grid + extent ('density'). Returns a summary.
        """
        empty = {"orgs_processed": 0, "control_zone_counts": {},
                 "polygons_layer": None, "density_grid": None,
                 "density_extent": None}
        if not isinstance(org_layer, MapLayer) or not org_layer.features:
            return empty
        geoms = _to_lonlat_geoms(org_layer)
        pts = [_point_xy(g) for g in geoms]
        n = len(pts)
        dist = [[float("inf")] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                if i != j and pts[i] is not None and pts[j] is not None:
                    dist[i][j] = _haversine_km(pts[i][0], pts[i][1],
                                               pts[j][0], pts[j][1])
        rings = sorted(float(r) for r in (buffer_rings_km or ()))
        self._attach_fields(org_layer, {"nearest_org_km": "number",
                                        "org_control_zone": "text"})
        counts = {}
        for i, feature in enumerate(org_layer.features):
            near = min(dist[i]) if pts[i] is not None else None
            feature.properties["nearest_org_km"] = (
                round(near, 3) if near is not None else None)
            zone = self._classify_zone(near, rings)
            feature.properties["org_control_zone"] = zone
            counts[zone] = counts.get(zone, 0) + 1
        result = {"orgs_processed": n, "control_zone_counts": counts,
                  "polygons_layer": None, "density_grid": None,
                  "density_extent": None}
        method = (method or "buffers").lower()
        if method == "density":
            grid, extent = _density_grid(org_layer, cells=density_cells)
            result["density_grid"] = grid
            result["density_extent"] = extent
        elif method == "voronoi":
            result["polygons_layer"] = _voronoi_layer(org_layer)
        else:
            result["polygons_layer"] = _buffer_rings_layer(org_layer, rings)
        return result

    def calculate_corridor_vulnerability(self, corridor_layer, org_layer,
                                         risk_radius_km=25.0):
        """Flag transport corridors passing within risk_radius_km of an org node."""
        empty = {"total": 0, "vulnerable": 0, "vulnerable_names": []}
        if (not isinstance(corridor_layer, MapLayer) or not corridor_layer.features
                or not isinstance(org_layer, MapLayer) or not org_layer.features):
            return empty
        line_geoms = _to_lonlat_geoms(corridor_layer)
        org_geoms = _to_lonlat_geoms(org_layer)
        org_pts = [_point_xy(g) for g in org_geoms]
        org_names = [f.get_name() for f in org_layer.features]
        self._attach_fields(corridor_layer, {"corridor_risk_km": "number",
                                             "nearest_org_name": "text",
                                             "corridor_vulnerable": "boolean"})
        vulnerable = 0
        vulnerable_names = []
        for i, feature in enumerate(corridor_layer.features):
            line = line_geoms.iloc[i] if i < len(line_geoms) else None
            best = float("inf")
            best_name = None
            for j, org_pt in enumerate(org_pts):
                if org_pt is None:
                    continue
                d = _point_line_min_km(org_pt[0], org_pt[1], line)
                if d < best:
                    best = d
                    best_name = org_names[j]
            flag = best < risk_radius_km
            feature.properties["corridor_risk_km"] = (
                round(best, 3) if best != float("inf") else None)
            feature.properties["nearest_org_name"] = best_name
            feature.properties["corridor_vulnerable"] = bool(flag)
            if flag:
                vulnerable += 1
                vulnerable_names.append(feature.get_name())
        return {"total": len(corridor_layer.features), "vulnerable": vulnerable,
                "vulnerable_names": vulnerable_names}

    # -- network cost surfaces --------------------------------------------
    # Great-circle proximity treats every corridor as a straight line.  A cost
    # surface, by contrast, assigns a traversal cost to every raster cell and a
    # least-cost path winds through the grid accumulating cost along the way
    # (denser / steeper / higher-friction ground is more expensive to cross).

    def _cost_array(self, layer, band=None):
        """Return a float64 cost array for a TerrainLayer band.

        Non-finite cells (NaN nodata) become barriers (inf) and negative cell
        values are clipped to 0 so the graph keeps non-negative edge weights.
        """
        if not isinstance(layer, TerrainLayer) or not layer.bands or layer.transform is None:
            return None
        if band is None:
            band = layer.active_band
        band = int(band)
        if not (1 <= band <= len(layer.bands)):
            return None
        arr = np.asarray(layer.bands[band - 1], dtype="float64").copy()
        arr = np.nan_to_num(arr, nan=np.inf, posinf=np.inf, neginf=-np.inf)
        arr = np.where(np.isfinite(arr) & (arr < 0.0), 0.0, arr)
        return arr

    def _snap_to_grid(self, layer, lon, lat):
        if layer.transform is None:
            return None
        try:
            row, col = rasterio.transform.rowcol(layer.transform, float(lon), float(lat))
        except Exception:
            return None
        h, w = layer.data.shape
        if not (0 <= row < h and 0 <= col < w):
            return None
        return (int(row), int(col))

    def _corridor_source_cells(self, cost_layer, line_geoms_by_layer):
        """Rasterize corridor lines into the set of grid cells they occupy.

        Every cell a corridor passes through becomes a Dijkstra source so a
        single multi-source run yields the least accumulated traversal cost
        from the whole transport network to every deposit.  Segments are
        sampled at half-cell spacing in case a straight line skips over cell
        centres.
        """
        if cost_layer.transform is None or cost_layer.data is None:
            return set()
        res_x, res_y = cost_layer.resolution or (1.0, 1.0)
        res_x = abs(res_x) or 1e-6
        res_y = abs(res_y) or 1e-6
        step = 0.5 * min(res_x, res_y)
        cells = set()
        for line_geoms in line_geoms_by_layer:
            for line_geom in line_geoms:
                if line_geom is None:
                    continue
                for coords in _iter_line_coords(line_geom):
                    if not coords:
                        continue
                    x0, y0 = (float(coords[0][0]), float(coords[0][1]))
                    cell = self._snap_to_grid(cost_layer, x0, y0)
                    if cell is not None:
                        cells.add(cell)
                    for x1, y1 in coords[1:]:
                        x1, y1 = float(x1), float(y1)
                        n = max(1, int(math.ceil(
                            max(abs(x1 - x0), abs(y1 - y0)) / step)) + 1)
                        for k in range(1, n + 1):
                            t = k / float(n)
                            cell = self._snap_to_grid(
                                cost_layer, x0 + (x1 - x0) * t,
                                y0 + (y1 - y0) * t)
                            if cell is not None:
                                cells.add(cell)
                        x0, y0 = x1, y1
        return cells

    def _dijkstra_from(self, cost, start, diagonal):
        """Single- or multi-source Dijkstra over a cost grid.

        start is either a (row, col) cell or an iterable of (row, col) source
        cells (e.g. every cell touched by a transport corridor).  Returns
        (dist, prev): dist[row, col] is the least accumulated cost from the
        nearest source cell; prev holds the flat index of the best predecessor
        cell (or -1 for the sources / unreachable cells).  Moving between cells
        costs the average of the two cells' costs, scaled by sqrt(2) for
        diagonals.  Barrier cells (non-finite cost) are skipped as sources.
        """
        if (isinstance(start, tuple) and len(start) == 2
                and isinstance(start[0], (int, np.integer))):
            starts = [start]
        else:
            starts = [tuple(s) for s in start]
        h, w = cost.shape
        inf = float("inf")
        dist = np.full((h, w), inf, dtype="float64")
        prev = np.full((h, w), -1, dtype="int64")
        heap = []
        for sr, sc in starts:
            if not (0 <= sr < h and 0 <= sc < w):
                continue
            if not np.isfinite(cost[sr, sc]):
                continue
            dist[sr, sc] = 0.0
            heapq.heappush(heap, (0.0, sr * w + sc))
        if diagonal:
            moves = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1),
                     (1, -1), (1, 0), (1, 1))
        else:
            moves = ((-1, 0), (1, 0), (0, -1), (0, 1))
        while heap:
            d, flat = heapq.heappop(heap)
            if d > dist[flat // w, flat % w]:
                continue
            r, c = divmod(flat, w)
            base_cost = cost[r, c]
            for dr, dc in moves:
                nr, nc = r + dr, c + dc
                if not (0 <= nr < h and 0 <= nc < w):
                    continue
                ncost = cost[nr, nc]
                if not np.isfinite(ncost):
                    continue
                if diagonal and dr != 0 and dc != 0:
                    step = (base_cost + ncost) * 0.5 * math.sqrt(2.0)
                else:
                    step = (base_cost + ncost) * 0.5
                nd = d + step
                if nd < dist[nr, nc]:
                    dist[nr, nc] = nd
                    prev[nr, nc] = flat
                    heapq.heappush(heap, (nd, nr * w + nc))
        return dist, prev

    def least_cost_path(self, cost_layer, start_lonlat, end_lonlat,
                        band=None, diagonal=True):
        """Least-cost path between two lon/lat points over a raster cost surface.

        start_lonlat / end_lonlat are (longitude, latitude) tuples.  Returns a
        dict with 'success', 'path' (list of [lon, lat] cell-centre coordinates),
        'cumulative_cost', 'distance_km' (geodesic length of the returned path),
        'cells' and (on failure) 'error'.
        """
        result = {"success": False, "path": [], "cumulative_cost": 0.0,
                  "distance_km": 0.0, "cells": 0, "error": None}
        cost = self._cost_array(cost_layer, band)
        if cost is None:
            result["error"] = "No usable cost raster."
            return result
        start = self._snap_to_grid(cost_layer, *start_lonlat)
        end = self._snap_to_grid(cost_layer, *end_lonlat)
        if start is None or end is None:
            result["error"] = "Start or end point is outside the cost raster."
            return result
        if cost.size > self._DIJKSTRA_CELL_LIMIT:
            result["error"] = ("Cost raster exceeds the pathfinding cell limit "
                               "(%d); use a smaller or reduced raster."
                               % self._DIJKSTRA_CELL_LIMIT)
            return result
        if not np.isfinite(cost[start]) or not np.isfinite(cost[end]):
            result["error"] = "Start or end cell is a barrier (no data)."
            return result
        dist, prev = self._dijkstra_from(cost, start, diagonal)
        if not np.isfinite(dist[end]):
            result["error"] = "No traversable path exists between the points."
            return result
        h, w = cost.shape
        path_flat = []
        cur = end[0] * w + end[1]
        while cur != -1:
            path_flat.append(cur)
            if cur == start[0] * w + start[1]:
                break
            cur = int(prev[cur // w, cur % w])
        path_flat.reverse()
        path = []
        for flat in path_flat:
            r, c = divmod(int(flat), w)
            x, y = rasterio.transform.xy(cost_layer.transform, r, c)
            path.append([float(x), float(y)])
        distance_km = 0.0
        for i in range(1, len(path)):
            distance_km += _haversine_km(path[i - 1][0], path[i - 1][1],
                                         path[i][0], path[i][1])
        result.update({
            "success": True,
            "path": path,
            "cumulative_cost": round(float(dist[end]), 4),
            "distance_km": round(distance_km, 4),
            "cells": len(path),
        })
        return result

    def cost_accumulation(self, cost_layer, source_lonlat, band=None,
                          diagonal=True):
        """Cumulative-cost (cost-distance) raster radiating from a source point.

        Returns a float64 array of the same shape as the band where each cell
        holds the least accumulated traversal cost from the source (inf where
        unreachable), or None if the cost surface cannot be processed.
        """
        cost = self._cost_array(cost_layer, band)
        if cost is None:
            return None
        start = self._snap_to_grid(cost_layer, *source_lonlat)
        if start is None or cost.size > self._DIJKSTRA_CELL_LIMIT:
            return None
        if not np.isfinite(cost[start]):
            return None
        dist, _ = self._dijkstra_from(cost, start, diagonal)
        return dist

    def least_cost_network(self, cost_layer, points_layer, mode="tree",
                           band=None, diagonal=True):
        """Connect a set of points with least-cost paths over a cost surface.

        mode 'chain' joins the points in their given order, 'star' joins every
        point to the first, and 'tree' builds a minimum-cost spanning network
        (Kruskal MST on the all-pairs least-cost graph).  Returns a (layer,
        summary) tuple: layer is a new MapLayer of LineString features carrying
        from_node, to_node, path_cost, path_distance_km and cells; summary holds
        node/path counts, total cost and total distance.
        """
        empty = {"nodes": 0, "paths": 0, "total_cost": 0.0,
                 "total_distance_km": 0.0, "mode": mode or "tree", "failures": []}
        if (not isinstance(points_layer, MapLayer) or not points_layer.features
                or not isinstance(cost_layer, TerrainLayer)
                or cost_layer.data is None):
            return None, empty
        geoms = _to_lonlat_geoms(points_layer)
        pts = [_point_xy(g) for g in geoms]
        names = [f.get_name() for f in points_layer.features]
        nodes = [(names[i], pts[i]) for i in range(len(pts)) if pts[i] is not None]
        if len(nodes) < 2:
            empty["nodes"] = len(nodes)
            return None, empty
        mode = (mode or "tree").lower()
        if mode == "chain":
            pairs = [(i, i + 1) for i in range(len(nodes) - 1)]
        elif mode == "star":
            pairs = [(0, i) for i in range(1, len(nodes))]
        else:
            mode = "tree"
            pairs = [(i, j) for i in range(len(nodes))
                     for j in range(i + 1, len(nodes))]
        results = {}
        for i, j in pairs:
            res = self.least_cost_path(cost_layer, nodes[i][1], nodes[j][1],
                                       band=band, diagonal=diagonal)
            if res["success"]:
                results[(i, j)] = res
        if mode in ("chain", "star"):
            chosen = [(i, j) for (i, j) in pairs if (i, j) in results]
        else:
            ordered = sorted((results[(i, j)]["cumulative_cost"], i, j)
                             for (i, j) in results)
            parent = list(range(len(nodes)))

            def _find(x):
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            chosen = []
            for _cost, i, j in ordered:
                ri, rj = _find(i), _find(j)
                if ri != rj:
                    parent[ri] = rj
                    chosen.append((i, j))
        layer = MapLayer.new_empty("Least Cost Network", crs="EPSG:4326",
                                   schema={"from_node": "text", "to_node": "text",
                                           "path_cost": "number",
                                           "path_distance_km": "number",
                                           "cells": "number"})
        total_cost = 0.0
        total_dist = 0.0
        for i, j in chosen:
            res = results[(i, j)]
            total_cost += res["cumulative_cost"]
            total_dist += res["distance_km"]
            layer.add_feature(
                "%s to %s" % (nodes[i][0], nodes[j][0]), "LineString",
                [list(c) for c in res["path"]],
                {"from_node": nodes[i][0], "to_node": nodes[j][0],
                 "path_cost": round(res["cumulative_cost"], 4),
                 "path_distance_km": round(res["distance_km"], 4),
                 "cells": res["cells"]})
        summary = {"nodes": len(nodes), "paths": len(chosen),
                   "total_cost": round(total_cost, 4),
                   "total_distance_km": round(total_dist, 4),
                   "mode": mode, "failures": []}
        return (layer if layer.features else None), summary


# ---------------------------------------------------------------------------
# Template Engine (standardized JSON templates for layers and features)
# ---------------------------------------------------------------------------

TEMPLATE_VERSION = "1.0"
TEMPLATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "mmapper_templates.json")


def _template_default_coerce(value, ftype):
    """Coerce a value to the canonical JSON representation for a field type."""
    ftype = str(ftype or "text").lower()
    if ftype == "boolean":
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() in ("1", "true", "yes", "y", "on")
    if ftype in ("number", "integer"):
        try:
            num = float(value)
            return int(num) if ftype == "integer" else num
        except (TypeError, ValueError):
            return 0
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return str(value)
    return value


def _json_safe(value):
    """Return a JSON-serializable copy of a value (handles numpy scalars)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, (dict,)):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return str(value)


def build_layer_template(layer, name=None, description=""):
    """Build a standardized layer template from a Layer (schema + properties + style)."""
    layer_name = name or getattr(layer, "name", None) or "Layer Template"
    fields = []
    for fname, ftype in (getattr(layer, "schema", None) or {}).items():
        fields.append({"name": fname, "type": ftype})
    properties = []
    for pname in (getattr(layer, "prop_types", None) or {}):
        properties.append({
            "name": pname,
            "type": getattr(layer, "prop_types", {}).get(pname, "text"),
            "value": _json_safe(getattr(layer, "properties", {}).get(pname)),
        })
    return {
        "mmapper_template": TEMPLATE_VERSION,
        "kind": "layer",
        "name": layer_name,
        "description": description or "",
        "crs": getattr(layer, "crs", None) or "EPSG:4326",
        "fields": fields,
        "properties": properties,
        "style": dict(layer.style_with_defaults() if hasattr(layer, "style_with_defaults") else {}),
    }


def build_feature_template(layer, feature, name=None, description=""):
    """Build a standardized feature template from one feature's properties."""
    schema = getattr(layer, "schema", None) or {}
    fields = []
    for key in (feature.properties if feature is not None else {}):
        ftype = schema.get(key, "text")
        fields.append({
            "name": key,
            "type": ftype,
            "default": _template_default_coerce(feature.properties.get(key), ftype),
        })
    tname = name or (feature.get_name() if feature is not None else "Feature Template")
    return {
        "mmapper_template": TEMPLATE_VERSION,
        "kind": "feature",
        "name": tname,
        "description": description or "",
        "fields": fields,
    }


def validate_template(data):
    """Validate and normalize a template dict into the canonical format.

    Accepts the standardized structure and (for convenience) a legacy
    {field_name: field_type} dict, which is wrapped as a layer template.
    Raises ValueError on structurally invalid input.
    """
    if not isinstance(data, dict):
        raise ValueError("Template must be a JSON object.")
    if data.get("mmapper_template") is None:
        if all(isinstance(v, str) for v in data.values()):
            data = {
                "mmapper_template": TEMPLATE_VERSION,
                "kind": "layer",
                "name": "Imported Template",
                "fields": [{"name": k, "type": v} for k, v in data.items()],
            }
        else:
            raise ValueError("Template is missing the 'mmapper_template' marker.")
    kind = str(data.get("kind", "layer"))
    if kind not in ("layer", "feature"):
        raise ValueError(f"Unknown template kind '{kind}'.")
    name = str(data.get("name") or "Untitled Template").strip()
    if not name:
        name = "Untitled Template"
    raw_fields = data.get("fields")
    if raw_fields is None:
        raw_fields = []
    if not isinstance(raw_fields, list):
        raise ValueError("Template 'fields' must be a list.")
    fields = []
    seen = set()
    for f in raw_fields:
        if not isinstance(f, dict):
            continue
        fname = str(f.get("name") or "").strip()
        if not fname or fname in seen:
            continue
        ftype = str(f.get("type") or "text").lower()
        if ftype not in TEMPLATE_FIELD_TYPES:
            ftype = "text"
        seen.add(fname)
        fields.append({
            "name": fname,
            "type": ftype,
            "default": _template_default_coerce(f.get("default"), ftype),
        })
    template = {
        "mmapper_template": str(data.get("mmapper_template") or TEMPLATE_VERSION),
        "kind": kind,
        "name": name,
        "description": str(data.get("description") or ""),
        "fields": fields,
    }
    if kind == "layer":
        raw_props = data.get("properties")
        if raw_props is None:
            raw_props = []
        if not isinstance(raw_props, list):
            raise ValueError("Template 'properties' must be a list.")
        properties = []
        seen_props = set()
        for p in raw_props:
            if not isinstance(p, dict):
                continue
            pname = str(p.get("name") or "").strip()
            if not pname or pname in seen_props:
                continue
            ptype = str(p.get("type") or "text").lower()
            if ptype not in TEMPLATE_FIELD_TYPES:
                ptype = "text"
            value = p.get("value")
            seen_props.add(pname)
            properties.append({
                "name": pname,
                "type": ptype,
                "value": _template_default_coerce(value, ptype) if value is not None else None,
            })
        template["crs"] = str(data.get("crs") or "EPSG:4326")
        template["properties"] = properties
        style = data.get("style")
        template["style"] = dict(style) if isinstance(style, dict) else {}
    return template


def template_to_json_string(template):
    """Serialize a (validated) template dict to a JSON string."""
    template = validate_template(template)
    return json.dumps(template, indent=2, ensure_ascii=False)


def template_to_json(template, filepath):
    """Write a template dict to a JSON file (standardized format)."""
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(template_to_json_string(template))


def template_from_json_string(text):
    """Parse and validate a template from a JSON string."""
    try:
        data = json.loads(text)
    except ValueError as e:
        raise ValueError(f"Invalid JSON: {e}") from e
    return validate_template(data)


def template_from_json(filepath):
    """Read and validate a template from a JSON file."""
    with open(filepath, "r", encoding="utf-8") as f:
        return template_from_json_string(f.read())


def create_layer_from_template(template, name=None, crs=None):
    """Create a new MapLayer from a layer template (schema, properties, style)."""
    template = validate_template(template)
    if template.get("kind") != "layer":
        raise ValueError("Template is not a layer template.")
    layer = MapLayer.new_empty(
        name or template.get("name") or "Imported Layer",
        crs or template.get("crs") or "EPSG:4326",
        {})
    for f in template.get("fields", []):
        layer.add_field(f["name"], f["type"], f.get("default"))
    for p in template.get("properties", []):
        value = p.get("value")
        layer.add_property(p["name"],
                           _template_default_coerce(value, p["type"])
                           if value is not None else None,
                           p["type"])
    if template.get("style"):
        layer.set_style(template["style"])
    return layer


def apply_feature_template(layer, feature, template):
    """Apply a feature template: ensure fields on the layer schema and feature.

    Returns the number of new fields added to the layer schema.
    """
    template = validate_template(template)
    if template.get("kind") != "feature":
        raise ValueError("Template is not a feature template.")
    added = 0
    for f in template.get("fields", []):
        if f["name"] not in layer.schema:
            layer.add_field(f["name"], f["type"], f.get("default"))
            added += 1
        if feature is not None and f["name"] not in feature.properties:
            feature.properties[f["name"]] = f.get("default")
    return added


def load_custom_templates():
    """Load the on-disk custom template library ({name: template})."""
    if not os.path.exists(TEMPLATE_FILE):
        return {}
    try:
        with open(TEMPLATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    result = {}
    for name, tpl in data.items():
        try:
            tpl = validate_template(tpl)
            result[tpl.get("name") or name] = tpl
        except ValueError:
            continue
    return result


def save_custom_templates(templates):
    """Persist the custom template library as a standardized JSON file."""
    data = {}
    for name, tpl in templates.items():
        try:
            data[name] = validate_template(tpl)
        except ValueError:
            continue
    with open(TEMPLATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


PROJECT_TYPE = "AccessibleGISProject"
PROJECT_VERSION = 2

_BAND_ENCODING = "zlib+base64"
_BAND_DTYPE = "float64"


def _encode_band(band):
    arr = np.asarray(band, dtype=_BAND_DTYPE)
    return {
        "encoding": _BAND_ENCODING,
        "dtype": _BAND_DTYPE,
        "shape": [int(arr.shape[0]), int(arr.shape[1])],
        "data": base64.b64encode(zlib.compress(arr.tobytes())).decode("ascii"),
    }


def _decode_band(item):
    if isinstance(item, dict) and item.get("encoding") == _BAND_ENCODING:
        raw = base64.b64decode(item["data"])
        arr = np.frombuffer(zlib.decompress(raw), dtype=item.get("dtype", _BAND_DTYPE))
        return np.asarray(arr.reshape(tuple(item["shape"])), dtype=_BAND_DTYPE)
    return np.array(item, dtype=_BAND_DTYPE)


def _layer_from_dict(data):
    kind = data.get("kind")
    if kind == "MapLayer":
        return MapLayer.from_dict(data)
    if kind == "TerrainLayer":
        return TerrainLayer.from_dict(data)
    layer = Layer(data.get("name", "New Layer"))
    layer.visible = bool(data.get("visible", True))
    layer.style = dict(DEFAULT_STYLE)
    layer.style.update(data.get("style", {}) or {})
    layer.properties = dict(data.get("properties", {}) or {})
    layer.prop_types = dict(data.get("prop_types", {}) or {})
    for child_data in data.get("children", []):
        child = _layer_from_dict(child_data)
        child.parent = layer
        layer.children.append(child)
    return layer


def save_project(filepath, layers):
    data = {
        "type": PROJECT_TYPE,
        "version": PROJECT_VERSION,
        "layers": [layer.to_dict() for layer in layers],
    }
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_project(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    if data.get("type") != PROJECT_TYPE:
        raise ValueError("Not an Accessible GIS project file.")
    return [_layer_from_dict(d) for d in data.get("layers", [])]
