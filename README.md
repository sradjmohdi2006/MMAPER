# MMAPER — Accessible GIS Viewer

> A keyboard-friendly, screen-reader-friendly desktop GIS for creating, editing, analysing and publishing vector and raster data.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)
![wxPython](https://img.shields.io/badge/GUI-wxPython%204.2%2B-green)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)
![Tests](https://img.shields.io/badge/tests-7%20suites%20passing-brightgreen)

MMAPER is a self-contained desktop geographic information system built with **Python**, **wxPython**, **GeoPandas**, **Shapely**, **Rasterio** and **Matplotlib**. It combines a live map canvas with a tree-based layer navigator and a list-based attribute reader, so the whole workflow remains usable without a mouse and without a spreadsheet grid.

The application, the data model, the seven test suites and the technical report in this repository are all part of the same project.

---

## Table of contents

- [Why MMAPER](#why-mmaper)
- [Feature highlights](#feature-highlights)
- [Requirements](#requirements)
- [Installation](#installation)
- [Running the application](#running-the-application)
- [Quick start tour](#quick-start-tour)
- [The interface](#the-interface)
- [Menus and keyboard shortcuts](#menus-and-keyboard-shortcuts)
- [Working with vector data](#working-with-vector-data)
- [Working with rasters](#working-with-rasters)
- [Live map, legend and styling](#live-map-legend-and-styling)
- [Spatial analysis](#spatial-analysis)
- [Templates](#templates)
- [Saving projects](#saving-projects)
- [Exporting](#exporting)
- [Architecture](#architecture)
- [Project file layout](#project-file-layout)
- [Tests](#tests)
- [Performance notes](#performance-notes)
- [Accessibility](#accessibility)
- [Known limitations and roadmap](#known-limitations-and-roadmap)
- [Technical report](#technical-report)
- [Contributing](#contributing)

---

## Why MMAPER

Most desktop GIS applications assume a mouse and a densely packed table of cells. MMAPER was designed around a different set of constraints:

- **One primary action per screen.** The layer tree, the attribute list and the map are separate, predictable regions.
- **List-based editing.** Attributes are read and edited row by row — one property, one value — instead of in a spreadsheet grid.
- **Keyboard-first navigation.** Every major action has a menu entry and, in most cases, a shortcut.
- **Immediate visual feedback.** A live matplotlib canvas redraws after every edit, with a legend that mirrors the layer tree.
- **Everything in one file.** A project (`.agis`) stores nested layers, layer properties, features, styles and raster pixels together.

The result is a compact GIS that is practical for data entry, quick overlay analysis and accessible demonstrations.

---

## Feature highlights

| Area | Capabilities |
| --- | --- |
| **Vector layers** | Create layers from scratch or import GeoJSON, Shapefile, KML, GeoPackage and other formats supported by GeoPandas |
| **Features** | Add, edit, duplicate, delete and copy Point, LineString and Polygon features; paste coordinates; edit geometry as text |
| **Attributes** | Typed field schema (`text`, `number`, `integer`, `boolean`), inline editing, field templates, area/length and centroid fields, X/Y coordinate splitting |
| **Layer nesting** | PDF-bookmark-style parent/child layers with drag-and-drop, cycle protection and undo |
| **Live map** | Matplotlib map canvas with automatic bounds, geographic aspect, legend, visibility toggles and z-order controls |
| **Styling** | Single colour, graduated choropleth by numeric field, or categorised unique values; markers, line width, fill opacity and colour maps |
| **Rasters** | Open GeoTIFF/DEM, create rasters from scratch, multi-band editing, RGB composites, colour-map rendering, elevation sampling |
| **Raster editing** | Fill, rectangle fill, add/multiply a constant, invert, directional gradients, Gaussian noise and 3×3 smoothing |
| **Spatial analysis** | Resource suitability, organisational influence zones (buffers/Voronoi/density), corridor vulnerability, least-cost paths and networks |
| **Templates** | 23 built-in field presets plus reusable custom layer/feature templates with a portable JSON schema |
| **Persistence** | Single `.agis` project file with nesting, properties, styles, bands (zlib + base64 compressed) and legacy compatibility |
| **Export** | Interactive Folium HTML web map, CSV attribute tables, GeoJSON and GeoTIFF |
| **Editing safety** | Snapshot-based undo/redo across vector, raster, style, property and nesting changes |
| **Accessibility** | Keyboard shortcuts, single-selection lists, descriptive status messages and a launcher script for Windows |

---

## Requirements

- **Python 3.10 or newer** (developed and tested with Python 3.13.10)
- **Windows** is the primary target; the launcher script is Windows-specific, but the Python code is portable
- A desktop session capable of running wxPython

Python dependencies are listed in [`requirements.txt`](requirements.txt):

| Package | Purpose |
| --- | --- |
| `wxPython >= 4.2.1` | Desktop GUI |
| `geopandas >= 0.14.0` | Vector I/O and CRS handling |
| `shapely >= 2.0.0` | Geometry operations |
| `pyproj >= 3.6.0` | Coordinate reference systems |
| `pandas >= 2.0.0` | Tabular data |
| `numpy >= 1.24.0` | Array and raster computation |
| `rasterio >= 1.3.0` | GeoTIFF/DEM reading and writing |
| `scipy >= 1.10.0` | Raster smoothing and Voronoi tessellation |
| `matplotlib >= 3.7.0` | Live map canvas, previews and colour maps |
| `folium >= 0.15.0` | Interactive HTML web-map export |
| `contextily >= 1.3.0` | Basemap support dependency |
| `reportlab >= 5.0.0` | PDF technical report generation (`_report.py`) |

> `data_model.py` automatically points `PROJ_LIB` and `GDAL_DATA` at the data shipped inside the installed `rasterio` / `pyproj` wheels before importing geospatial libraries. This avoids the usual "PROJ database not found" problem on Windows.

---

## Installation

```powershell
git clone https://github.com/sradjmohdi2006/MMAPER.git
cd MMAPER

py -m venv venv
venv\Scripts\activate

python -m pip install --upgrade pip
pip install -r requirements.txt
```

On Linux or macOS the equivalent virtual-environment commands are:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

wxPython may require platform-specific wheels; see the [wxPython download page](https://wxpython.org/pages/downloads/) if `pip` cannot find a binary wheel for your platform.

---

## Running the application

From an activated virtual environment:

```powershell
python main.py
```

Or double-click **`Launch Mapper.bat`**, which runs `venv\Scripts\python.exe main.py` from the project folder.

A sample project is not bundled, but two small datasets are:

- `sample.geojson` — two vector features (a point and a polygon)
- `sample_dem.tif` — a tiny terrain raster used by the raster tests

---

## Quick start tour

1. **Launch** the application. An empty map canvas appears on the left and an empty layer tree on the right.
2. Choose **File → Open GeoJSON…** (`Ctrl+O`) and select `sample.geojson`.
3. Select the new layer in the tree. The live map renders both features and the legend lists the layer.
4. Select a feature in the tree. Its attributes appear in the lower-right list.
5. Double-click an attribute value to edit it, or press `Ctrl+F` to open the **Fields List** for bulk value editing.
6. Try **Layer → Layer Style…** (`Ctrl+Shift+Y`) and switch to **Graduated** styling using a numeric field.
7. Add a raster with **Raster → New Terrain/Raster…** and edit it with **Raster → Edit Raster Values…**.
8. Save everything with **File → Save Project As…** (`Ctrl+Alt+Shift+S`), producing a single `.agis` file.
9. Publish the visible vector layers with **File → Export Web Map (HTML)…** (`Ctrl+E`).

---

## The interface

The main window is split into three cooperating regions:

```
┌───────────────────────────────┬──────────────────────────────┐
│                               │  Layer tree                  │
│                               │  (nesting, visibility,       │
│        Live map canvas        │   selection, drag & drop)    │
│        + legend               │                              │
│                               ├──────────────────────────────┤
│                               │  Property / attribute list   │
│                               │  (one row per field/value)   │
└───────────────────────────────┴──────────────────────────────┘
```

- **Map canvas** — draws every effectively visible layer, auto-scales to the data bounds, keeps a true aspect ratio for geographic coordinates and updates after edits. The legend beside the map lists layers in draw order.
- **Layer tree** — shows layers and their child layers, then features. Hidden layers are greyed out and suffixed with `(hidden)`. Right-click for the context menu; drag a layer onto another to re-nest it.
- **Property list** — shows the selected layer summary or the selected feature's attributes. `Enter` or double-click opens an editor. The status bar reports the result of each action.

The vertical splitter between the tree and the list can be resized. The map pane can be hidden entirely from **View → Map Pane** (`Ctrl+M`).

---

## Menus and keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| `Ctrl+Alt+N` | New project |
| `Ctrl+Alt+O` | Open project (`.agis`) |
| `Ctrl+Alt+S` | Save project |
| `Ctrl+Alt+Shift+S` | Save project as |
| `Ctrl+Shift+N` | New vector layer |
| `Ctrl+O` | Open GeoJSON |
| `Ctrl+Shift+O` | Add vector layer (Shapefile, KML, GPKG, …) |
| `Ctrl+T` | Open terrain raster |
| `Ctrl+S` / `Ctrl+Shift+S` | Save current layer / Save As |
| `Ctrl+E` | Export web map (HTML) |
| `Ctrl+D` | Export attribute table (CSV) |
| `Ctrl+Q` | Exit |
| `Ctrl+Z` / `Ctrl+Shift+Z` | Undo / redo |
| `Ctrl+N` | Add feature |
| `Ctrl+G` | Edit geometry |
| `Ctrl+Shift+D` | Duplicate feature |
| `Delete` | Delete feature |
| `Ctrl+P` | Add/edit feature property |
| `Ctrl+Shift+C` | Copy feature to another layer |
| `Ctrl+L` | Query elevation from the current terrain layer |
| `Ctrl+M` | Show/hide the map pane |
| `F2` | Rename layer |
| `V` | Show/hide layer |
| `Ctrl+Shift+Y` | Layer Style |
| `Alt+Up` / `Alt+Down` | Move layer up/down in draw order |
| `Ctrl+F` | Open the Fields List |
| `Ctrl+Shift+T` | New terrain/raster |
| `Ctrl+R` | Edit raster values |
| `Ctrl+Shift+P` | Preview raster as an image |
| `Ctrl+Alt+M` | Raster style (colour map / RGB) |
| `Ctrl+Alt+B` | Manage raster bands |

### Menu overview

| Menu | Commands |
| --- | --- |
| **File** | New/Open/Save/Save As project; new layer; open GeoJSON; add vector layer; open terrain; save layer; export web map; export CSV; exit |
| **Edit** | Undo/redo; add/edit/duplicate/delete feature; property editing; copy feature; elevation query |
| **View** | Toggle the live map pane |
| **Layer** | Layer properties; show/hide; style; move up/down; remove/rename/duplicate; CRS; field schema; templates; computed fields |
| **Raster** | New raster; edit values; preview; style; manage bands |
| **Spatial** | Buffer; simplify; split coordinates; resource suitability; org network reach; network cost surfaces |

The layer tree also has a right-click context menu that gathers the most common layer and feature operations.

---

## Working with vector data

### Layers

- **New Layer** (`Ctrl+Shift+N`) creates a blank vector layer with a name, CRS and an optional field template.
- **Open GeoJSON** (`Ctrl+O`) and **Add Vector Layer** (`Ctrl+Shift+O`) import existing data through GeoPandas.
- Any layer can contain **child layers**. When a layer is already selected, MMAPER asks whether a new layer should become its child or a sibling. Layers can also be re-nested by dragging them in the tree.
- Layer **visibility** is independent of nesting: hiding a parent hides its descendants on the map and in the web-map export.
- Each layer carries its own **properties** (name/value/type), useful for metadata such as planet length, terrain class or project phase. Open them with **Layer → Layer Properties…**.
- **Set CRS** assigns a coordinate reference system without moving geometries; **Reproject Layer** transforms the geometries into a new CRS.

### Features and attributes

The **Add Feature** dialog accepts points typed one per line, pasted coordinate lists or interactive entry, and assembles Point, LineString and Polygon geometries (polygon rings are closed automatically).

Feature attributes are typed and editable. **Fields List** (`Ctrl+F`) provides a searchable, single-selection list of every field and value for the selected feature, the whole layer, or a layer-level property group. Values are coerced to their declared type, and simple unit expressions such as `12 km` or `1,250` are parsed sensibly.

Computed fields include:

- **Area/Length** fields for every feature
- **Centroid** coordinates
- **X/Y** columns split from geometry or from a point-like property

Buffers and simplification are applied to individual features through the **Spatial** menu.

---

## Working with rasters

`TerrainLayer` stores one or more independent `float64` bands in memory, together with bounds, resolution, an affine transform, a CRS and per-band statistics.

- **Open Terrain Raster** reads GeoTIFF/DEM files.
- **New Terrain/Raster** creates a raster from scratch with a chosen size, bounds or resolution, starting value and band count.
- **Edit Raster Values** supports:
  - fill the whole active band
  - fill a rectangular row/column block
  - add or multiply by a constant
  - invert elevations (`min + max − value`)
  - add a linear gradient in six directions
  - add Gaussian noise
  - smooth with repeated 3×3 averaging
- **Manage Bands** (`Ctrl+Alt+B`) sets the active band, adds, duplicates, renames and removes bands.
- **Preview Raster** renders any band through a colour map or any three bands as an RGB composite, with a 2 %–98 % percentile stretch.
- **Raster Style** controls how the raster is drawn on the live map.
- **Query Elevation** (`Ctrl+L`) samples the active band at a feature location using the raster transform.

Rasters can be saved back to GeoTIFF, and they are embedded in `.agis` project files. Bands are compressed with zlib and base64, which keeps project files small while remaining compatible with version 1 projects that stored plain nested lists.

### Large rasters

Rasters larger than **32 million cells** are decimated at load time to keep memory use and rendering responsive. The original dimensions are retained in the layer summary (`original_shape`, `reduced`), while the working copy is used for display and analysis. Canvas rendering additionally downsamples to a maximum of **1024 pixels** per axis.

---

## Live map, legend and styling

The map canvas renders visible vector layers and rasters using Matplotlib:

- Automatic extent calculation across all visible layers
- Equal aspect ratio when coordinates are geographic
- Points, lines and polygons drawn with per-layer colour, line width, fill opacity, marker type and marker size
- Terrain drawn with a selectable colour map or an RGB band composite
- Graduated and categorised vector rendering driven by attributes

The **legend** beside the canvas lists every layer in draw order, shows a visibility checkbox and colour swatch, and provides **Move Up** / **Move Down** buttons. Visibility and order are also available from the **Layer** menu, the tree context menu and `Alt+Up` / `Alt+Down`. All of these changes are undoable.

The **Layer Style** dialog includes a live preview and offers three modes:

| Mode | Description |
| --- | --- |
| **Single colour** | One colour, line width, fill opacity, marker and marker size |
| **Graduated (colours)** | Numeric attribute field divided into 2–20 classes over a colour map |
| **Categorised (categories)** | One colour per distinct text value |

Colour maps include `terrain`, `viridis`, `plasma`, `inferno`, `magma`, `cividis`, `turbo`, `coolwarm`, greys and several sequential/diverging palettes.

---

## Spatial analysis

All analysis is implemented in `SpatialAnalysisEngine` (`data_model.py`) and attaches its results directly to the source features, so scores are immediately visible in the tree, the attribute list and the map.

### Resource suitability analysis

Scores resource deposits for extraction or infrastructure siting:

```
proximity = max(0, 1 − distance_to_infrastructure_km / max_distance_km)

score = proximity                                   (no cost surface)
score = proximity × (1 − weight) + reach × weight   (with cost surface)
score = score × terrain_mask                        (slope/elevation limits)

reach = max(0, 1 − network_reach_cost / normaliser)
```

Fields added: `suitability_score`, `distance_to_infra_km`, `suitability_excluded`, plus optional `elevation_m`, `slope_pct`, `network_reach_cost` and `network_reach_score`.

### Organisational network reach

Partitions influence around organisational nodes using one of three methods:

- **Buffers** — concentric great-circle rings (default `10, 50, 100` km)
- **Voronoi** — Thiessen cells assigning every area to its nearest node
- **Density** — a heat grid of node concentration

The engine can also run **corridor vulnerability**, flagging transport features that pass within a risk radius of an organisational node and writing a `corridor_risk` field.

### Network cost surfaces

Any raster band can act as a traversal-cost surface. NaN cells are treated as barriers. The engine provides:

- **Least-cost path** between two points (Dijkstra over the cell grid, optional diagonals)
- **Cost accumulation** — a cumulative-cost surface radiating from one source
- **Least-cost networks** — chain (points in order), star (hub = first point) or minimum-cost spanning tree

Paths are returned as new LineString layers with `path_cost`, `path_distance_km` and `cells` fields.

> Analysis uses great-circle (haversine) distances and cell-grid traversal. Results are intended for rapid screening and visual comparison, not survey-grade routing.

---

## Templates

Templates make layer and feature schemas portable between projects and machines.

- **23 built-in field presets**: Blank, Demographics, Economy, Terrain & Nature, Infrastructure, Administrative, Health Services, Education, Transport Network, Utilities & Energy, Water Resources, Emergency Services, Agriculture & Forestry, Environment & Conservation, Mining & Resources, Urban Development, Climate & Weather, Security & Defence, Trade & Commerce, Telecommunications, Housing & Real Estate, Heritage & Culture, Tourism & Recreation.
- **Custom templates** save the fields of any layer or feature to `mmapper_templates.json` and appear in the template dialogs.
- **Export/Import** writes or reads a single `.json` template file.
- Templates are validated and normalised to one canonical schema:

```json
{
  "mmapper_template": "1.0",
  "kind": "layer",
  "name": "Mining & Resources",
  "description": "Mineral deposits and extraction sites",
  "fields": [
    { "name": "name", "type": "text", "default": "" },
    { "name": "reserve_estimate", "type": "number", "default": null },
    { "name": "active", "type": "boolean", "default": true }
  ]
}
```

Field types are restricted to `text`, `number`, `integer` and `boolean`; defaults are coerced to the declared type. Legacy `{field: type}` dictionaries are accepted on import and wrapped as layer templates.

---

## Saving projects

**File → Save Project** writes the entire workspace to a single `.agis` JSON file:

```json
{
  "type": "AccessibleGISProject",
  "version": 2,
  "layers": [
    {
      "kind": "map",
      "name": "Planet",
      "visible": true,
      "style": { "...": "..." },
      "properties": { "planet_length": 12742 },
      "prop_types": { "planet_length": "number" },
      "children": [ { "kind": "map", "name": "Continent", "features": [ "..." ] } ],
      "features": [],
      "crs": "EPSG:4326",
      "schema": { "name": "text" }
    }
  ]
}
```

- Layer nesting, properties, styles, visibility and feature schemas are preserved.
- Vector features are stored inline as GeoJSON geometry plus properties.
- Raster bands are stored as base64-encoded zlib streams with shape, bounds, transform, CRS, band names and the active band.
- Version 1 projects (plain nested-list bands) still load correctly.
- A missing external raster file is tolerated: the layer metadata is retained and the stored pixels are used when present.

Projects can contain large rasters; the compression keeps files practical, but a project with a very large DEM will still be correspondingly large. The repository deliberately excludes `*.agis` files and the local `knowledge_graph.db`.

---

## Exporting

| Export | Command | Output |
| --- | --- | --- |
| **Web map** | `Ctrl+E` | Interactive Folium HTML map with one toggleable GeoJSON group per visible layer |
| **Attribute table** | `Ctrl+D` | CSV of the selected vector layer |
| **Layer** | `Ctrl+S` | GeoJSON (vector) or GeoTIFF (raster) |
| **Template** | Layer menu | Portable JSON layer/feature template |

Hidden layers are omitted from the HTML web-map export. Raster layers are not part of the Folium export.

---

## Architecture

MMAPER intentionally has no application framework. The GUI is plain wxPython and the data layer is built directly on GeoPandas, Shapely, Rasterio and NumPy.

```
MMAPER/
├── main.py                  # wxPython application: window, menus, dialogs, handlers
├── data_model.py            # Data classes, raster ops, spatial engine, persistence
├── requirements.txt
├── Launch Mapper.bat        # Windows launcher using the local venv
├── sample.geojson           # Small vector sample
├── sample_dem.tif           # Small raster sample
├── MMAPER_Report.pdf        # Generated technical report
├── _report.py               # ReportLab report generator
├── _smoke_test.py           # Vector / attribute / UI / styling tests
├── _nesting_test.py         # Nesting, drag/drop, visibility, properties tests
├── _project_test.py         # Project save/load round-trip tests
├── _raster_test.py          # Raster creation, editing, bands, compression tests
├── _spatial_analysis_test.py# Analysis engine and analysis dialogs tests
├── _template_test.py        # Template engine and template dialogs tests
└── _fields_test.py          # Field-list editing and unit parsing tests
```

### Module responsibilities

| Module | Responsibility |
| --- | --- |
| `data_model.py` | `Layer`, `Feature`, `MapLayer`, `TerrainLayer`, `SpatialAnalysisEngine`; template engine; `.agis` save/load; PROJ/GDAL environment setup |
| `main.py` | `MainFrame`, `MapCanvasPanel` and ~30 dialogs; all menus, shortcuts and event handlers |

### Class hierarchy

```
Layer                      name, children, parent, properties, prop_types,
├── MapLayer               visible, style
│                          features, schema, crs, filepath
└── TerrainLayer           bands, band_names, active_band, transform,
                           bounds, resolution, stats, original_shape, reduced

Feature                    type, geometry (GeoJSON dict), properties
SpatialAnalysisEngine      suitability, influence zones, corridor risk,
                           least-cost paths / accumulation / networks
```

### Data flow

A typical edit follows a simple path:

1. A menu item or shortcut fires a wx event handler in `MainFrame`.
2. The handler opens a modal dialog and validates the input.
3. The handler mutates the selected `Layer`, `Feature` or `TerrainLayer`.
4. The tree node and property list are refreshed; the map canvas re-renders.
5. The status bar reports what changed. Snapshot undo is available throughout.

---

## Tests

The repository contains **seven standalone test scripts** that exercise the data model, the GUI dialogs and the map canvas. They use the real wxPython controls and print one line per assertion group; no external test runner is required.

Run all of them from the project root with the virtual environment active:

```powershell
python _smoke_test.py
python _nesting_test.py
python _project_test.py
python _raster_test.py
python _spatial_analysis_test.py
python _template_test.py
python _fields_test.py
```

Or in one line:

```powershell
Get-ChildItem _*_test.py | ForEach-Object { python $_.FullName }
```

| Test suite | Coverage |
| --- | --- |
| `_smoke_test.py` | Feature creation, coordinate parsing, field templates, coordinate splitting, buffers/simplification, GeoJSON round-trip, reprojection, field list, undo/redo, styling, map pane, legend, z-order |
| `_nesting_test.py` | Parent/child layers, cycle protection, detach, layer properties, drag-and-drop re-nesting, visibility, nested draw order |
| `_project_test.py` | Project save/load, nesting and features round-trip, raster metadata, visibility/style persistence, tree rebuild, invalid project rejection |
| `_raster_test.py` | Every raster operation, elevation sampling, GeoTIFF round-trip, compression and legacy bands, multi-band management, RGB rendering, band dialogs |
| `_spatial_analysis_test.py` | Suitability scoring with and without terrain/cost surface, all weight cases, Voronoi/buffer/density zones, corridor vulnerability, least-cost path/accumulation/network, analysis dialogs |
| `_template_test.py` | Built-in presets, layer/feature templates, validation, coercion, JSON round-trip, custom template library, template dialogs |
| `_fields_test.py` | Number parsing, feature and layer-property modes, feature switching, unit handling, inline editing |

At the time of writing, **all seven suites pass** (Python 3.13.10, wxPython 4.3.1, GeoPandas 1.1.4, Rasterio 1.5.0). wx may print harmless Windows `TreeView`/`SetFocus` diagnostics during GUI tests; the tests still complete successfully.

---

## Performance notes

- Raster bands are held as float64 arrays; rasters above **32 million cells** are decimated on load.
- The canvas renders at most **1024 × 1024** samples, so redraws remain interactive.
- Project files compress each raster band with **zlib → base64**, with the original shape recorded for reconstruction.
- Dijkstra-based analysis is bounded by an internal cell limit to prevent accidental multi-minute runs on very large cost surfaces.
- The undo system stores deep snapshots. Very large projects with many raster edits will consume memory proportionally; save and reopen a project to reset history if needed.

---

## Accessibility

MMAPER was built with assistive technology in mind:

- Tree and single-selection list controls instead of editable grid controls
- Complete menu access with underlined mnemonics and documented shortcuts
- Status-bar confirmations for actions and validation errors in dialogs
- Layer visibility reflected both in the tree label (`(hidden)`) and by greyed-out text
- A dedicated **Fields List** workflow that lets a user move through fields and values without navigating a table cell by cell
- The map is an enhancement rather than the only way to inspect data; attributes remain fully readable in the list

The application has been developed on Windows and is intended to work with screen readers such as NVDA and with keyboard-only navigation. Contributions that improve accessibility are especially welcome.

---

## Known limitations and roadmap

- **Windows-first.** Packaging and the launcher target Windows; Linux/macOS users run from source.
- **No basemap tiles** in the live canvas (Contextily is available as a dependency but not wired into the canvas).
- **Raster analysis is in-memory.** Very large DEMs are decimated, so analysis uses the reduced working copy.
- **Approximate measurements.** Distances use haversine great-circle calculations; areas and buffers are approximated in EPSG:4326 unless a projected CRS is used.
- **Folium export covers vector layers only**; rasters are not rendered into the HTML map.
- **No database backend.** Projects are single JSON documents, which is simple and portable but not designed for concurrent editing.
- **Undo depth is memory-bound** rather than capped by a fixed number of steps.

Possible next steps: basemap support, raster clipping/reprojection tools, GeoPackage project storage, a plugin API for analysis routines, and a packaged Windows installer.

---

## Technical report

[`MMAPER_Report.pdf`](MMAPER_Report.pdf) is a detailed technical report generated by `_report.py`. It documents the architecture, every data-model class and method, the GUI dialogs, the project and template formats, the spatial analysis algorithms, test coverage and code statistics.

Regenerate it after major changes with:

```powershell
python _report.py
```

The report uses ReportLab and writes `MMAPER_Report.pdf` into the project root.

---

## Contributing

1. Fork the repository and create a feature branch.
2. Keep the two-module structure and the existing naming/style conventions.
3. Add or update a test script for behaviour changes, and run all seven suites.
4. Describe user-visible changes and accessibility impact in the pull request.

Bug reports are most useful when they include the Python and package versions, the steps to reproduce, the contents of any project file that triggers the issue, and the status-bar or console output.

---

<p align="center"><strong>MMAPER</strong> — mapping without barriers.</p>
