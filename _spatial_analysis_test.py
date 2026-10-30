import numpy as np
import wx

import main
from data_model import (MapLayer, TerrainLayer, SpatialAnalysisEngine,
                        compute_spatial_suitability)

app = wx.App(False)
frame = main.MainFrame()

# --- shared fixture layers (EPSG:4326) ---
# A transport corridor along lat 40 from lon 30 to 31.
lines = MapLayer.new_empty("Corridors", "EPSG:4326", {"name": "text"})
lines.add_feature("Road", "LineString", [[30.0, 40.0], [31.0, 40.0]], {"name": "Road"})

# Deposit right on the corridor, and one ~111 km north of it.
deposits = MapLayer.new_empty("Deposits", "EPSG:4326", {"name": "text"})
deposits.add_feature("On Road", "Point", [30.0, 40.0], {"name": "On Road"})
deposits.add_feature("Far North", "Point", [30.0, 41.0], {"name": "Far North"})

# --- module-level proximity scoring ---
scores = compute_spatial_suitability(deposits, lines, max_distance_km=50.0)
assert len(scores) == 2
assert scores[0] == 1.0
assert scores[1] == 0.0
mid = compute_spatial_suitability(
    MapLayer.new_empty("M", "EPSG:4326", {"name": "text"}),
    lines, 50.0)
assert mid == []
assert compute_spatial_suitability(deposits,
                                   MapLayer.new_empty("E", "EPSG:4326",
                                                      {"name": "text"})) == []
print("compute_spatial_suitability OK")

# --- engine: distance-only suitability ---
engine = SpatialAnalysisEngine()
res = engine.compute_resource_suitability(deposits, transport_layers=[lines],
                                          max_distance_km=50.0)
assert res["analyzed"] == 2 and res["excluded"] == 0
assert res["best"] == "On Road" and res["best_score"] == 1.0
assert res["min_score"] == 0.0 and res["max_score"] == 1.0
assert res["mean_score"] == 0.5
assert "suitability_score" in deposits.schema
assert "distance_to_infra_km" in deposits.schema
assert "suitability_excluded" in deposits.schema
f0, f1 = deposits.features
assert f0.properties["suitability_score"] == 1.0
assert f0.properties["distance_to_infra_km"] == 0.0
assert f0.properties["suitability_excluded"] is False
assert f1.properties["suitability_score"] == 0.0
assert abs(f1.properties["distance_to_infra_km"] - 111.32) < 0.5
assert f1.properties["suitability_excluded"] is False
print("compute_resource_suitability (distance) OK")

# --- no transport layers: full proximity, no infra distance ---
deposits2 = MapLayer.new_empty("Deposits2", "EPSG:4326", {"name": "text"})
deposits2.add_feature("A", "Point", [30.0, 40.0], {"name": "A"})
res2 = engine.compute_resource_suitability(deposits2, transport_layers=[])
assert res2["analyzed"] == 1 and res2["max_score"] == 1.0
assert deposits2.features[0].properties["distance_to_infra_km"] is None
print("compute_resource_suitability (no corridors) OK")

# --- empty deposits: summary with no analysis ---
empty_summary = engine.compute_resource_suitability(
    MapLayer.new_empty("Empty", "EPSG:4326", {"name": "text"}),
    transport_layers=[lines])
assert empty_summary["analyzed"] == 0
assert empty_summary["best"] is None and empty_summary["best_score"] == 0.0
print("compute_resource_suitability (empty) OK")

# --- terrain: slope exclusion ---
slope_terrain = TerrainLayer.new_empty("Slope", "EPSG:4326", width=10, height=10,
                                       bounds=(30.0, 39.0, 31.0, 40.0))
slope_terrain.add_gradient("North->South", 0, 100)
tdep = MapLayer.new_empty("TD", "EPSG:4326", {"name": "text"})
tdep.add_feature("Steep", "Point", [30.25, 39.75], {"name": "Steep"})
tres = engine.compute_resource_suitability(tdep, transport_layers=[lines],
                                           terrain_layer=slope_terrain,
                                           max_slope_pct=5.0)
assert "elevation_m" in tdep.schema and "slope_pct" in tdep.schema
sf = tdep.features[0].properties
assert sf["suitability_excluded"] is True
assert sf["suitability_score"] == 0.0
assert sf["slope_pct"] is not None and sf["elevation_m"] is not None
tres_no_limit = engine.compute_resource_suitability(
    tdep, transport_layers=[lines], terrain_layer=slope_terrain,
    max_slope_pct=None)
assert tres_no_limit["excluded"] == 0
assert tres_no_limit["best_score"] > 0.0
print("compute_resource_suitability (terrain slope) OK")

# --- terrain: elevation window exclusion ---
flat = TerrainLayer.new_empty("Flat", "EPSG:4326", width=10, height=10,
                              bounds=(30.0, 39.0, 31.0, 40.0))
flat.fill(50)
ele = MapLayer.new_empty("Ele", "EPSG:4326", {"name": "text"})
ele.add_feature("Low", "Point", [30.25, 39.75], {"name": "Low"})
low_res = engine.compute_resource_suitability(ele, terrain_layer=flat,
                                              min_elevation=60.0)
assert low_res["excluded"] == 1
assert ele.features[0].properties["suitability_excluded"] is True
high_res = engine.compute_resource_suitability(ele, terrain_layer=flat,
                                               max_elevation=40.0)
assert high_res["excluded"] == 1
assert ele.features[0].properties["elevation_m"] == 50.0
print("compute_resource_suitability (terrain elevation) OK")

# --- cost-weighted network reach in suitability ---
# 20x20 cost grid over lon 29.5-31.5 / lat 39.5-41.5 (0.1 deg/cell), uniform 1.0.
# The 'lines' corridor (lat 40, lon 30-31) rasterizes to a strip of source
# cells, so reach cost = 0 on the corridor, ~5 at Mid, ~10 at Far North.
cost_surf = TerrainLayer.new_empty("Cost", "EPSG:4326", width=20, height=20,
                                   bounds=(29.5, 39.5, 31.5, 41.5))
cost_surf.fill(1.0)
cdep = MapLayer.new_empty("CDep", "EPSG:4326", {"name": "text"})
cdep.add_feature("On Road", "Point", [30.0, 40.0], {"name": "On Road"})
cdep.add_feature("Mid", "Point", [30.5, 40.5], {"name": "Mid"})
cdep.add_feature("Far North", "Point", [30.0, 41.0], {"name": "Far North"})

# weight 0 => identical scores to plain proximity, but reach fields attached.
w0 = engine.compute_resource_suitability(cdep, transport_layers=[lines],
                                         cost_layer=cost_surf, cost_weight=0.0)
assert "network_reach_cost" in cdep.schema
assert "network_reach_score" in cdep.schema
props = {f.get_name(): f.properties for f in cdep.features}
assert props["On Road"]["suitability_score"] == 1.0
assert props["Far North"]["suitability_score"] == 0.0
assert props["On Road"]["network_reach_cost"] == 0.0
assert props["On Road"]["network_reach_score"] == 1.0
assert props["Far North"]["network_reach_score"] == 0.0
assert abs(props["Mid"]["network_reach_cost"] - 5.0) < 0.1
assert abs(props["Far North"]["network_reach_cost"] - 10.0) < 0.1
print("compute_resource_suitability (reach, weight 0) OK")

# weight 1 => score is purely the normalized reach score.
w1 = engine.compute_resource_suitability(cdep, transport_layers=[lines],
                                         cost_layer=cost_surf, cost_weight=1.0)
assert w1["analyzed"] == 3 and w1["excluded"] == 0
assert w1["best"] == "On Road" and w1["best_score"] == 1.0
assert w1["min_score"] == 0.0 and w1["max_score"] == 1.0
assert abs(w1["mean_score"] - 0.5) < 1e-3
w1p = {f.get_name(): f.properties for f in cdep.features}
assert abs(w1p["On Road"]["suitability_score"] - 1.0) < 1e-9
assert abs(w1p["Mid"]["suitability_score"] - 0.5) < 0.02
assert abs(w1p["Far North"]["suitability_score"] - 0.0) < 1e-9
assert abs(w1p["Mid"]["network_reach_score"] - 0.5) < 0.02
assert abs(w1p["Far North"]["network_reach_score"] - 0.0) < 1e-9
print("compute_resource_suitability (reach, weight 1) OK")

# weight 0.5 => each score sits between the proximity-only and reach-only values.
wblend = engine.compute_resource_suitability(cdep, transport_layers=[lines],
                                             cost_layer=cost_surf, cost_weight=0.5)
assert wblend["best"] == "On Road" and abs(wblend["best_score"] - 1.0) < 1e-9
assert abs(wblend["max_score"] - 1.0) < 1e-9 and abs(wblend["min_score"] - 0.0) < 1e-9
bp = {f.get_name(): f.properties["suitability_score"] for f in cdep.features}
assert abs(bp["On Road"] - 1.0) < 1e-9
assert abs(bp["Mid"] - 0.25) < 0.02
assert abs(bp["Far North"] - 0.0) < 1e-9
print("compute_resource_suitability (reach, weight 0.5) OK")

# max_reach_cost override: threshold 4 => Mid and Far North clamp to 0.
ovr = engine.compute_resource_suitability(cdep, transport_layers=[lines],
                                          cost_layer=cost_surf, cost_weight=1.0,
                                          max_reach_cost=4.0)
assert ovr["best"] == "On Road" and ovr["best_score"] == 1.0
assert ovr["max_score"] == 1.0 and ovr["min_score"] == 0.0
assert all(f.properties["network_reach_score"] in (0.0, 1.0)
           for f in cdep.features)
print("compute_resource_suitability (reach, max_reach_cost) OK")

# outside the raster or unreachable => reach_cost None / reach_score 0.
outdep = MapLayer.new_empty("OutDep", "EPSG:4326", {"name": "text"})
outdep.add_feature("Outside", "Point", [50.0, 50.0], {"name": "Outside"})
outres = engine.compute_resource_suitability(outdep, transport_layers=[lines],
                                             cost_layer=cost_surf, cost_weight=1.0)
assert outdep.features[0].properties["network_reach_cost"] is None
assert outdep.features[0].properties["network_reach_score"] == 0.0
assert outdep.features[0].properties["suitability_score"] == 0.0
enclosed = TerrainLayer.new_empty("Enclosed", "EPSG:4326", width=20, height=20,
                                  bounds=(29.5, 39.5, 31.5, 41.5))
enclosed.fill(1.0)
enclosed.bands[0][8:13, 8:13] = np.nan
enclosed.bands[0][10, 10] = 1.0
entrap = MapLayer.new_empty("Entrap", "EPSG:4326", {"name": "text"})
entrap.add_feature("Boxed In", "Point", [30.5, 40.5], {"name": "Boxed In"})
tres_en = engine.compute_resource_suitability(
    entrap, transport_layers=[lines], cost_layer=enclosed, cost_weight=1.0)
assert entrap.features[0].properties["network_reach_cost"] is None
assert entrap.features[0].properties["network_reach_score"] == 0.0
assert entrap.features[0].properties["suitability_score"] == 0.0
print("compute_resource_suitability (reach, outside/unreachable) OK")

# no cost surface => unchanged base behaviour, no reach fields.
nodep = MapLayer.new_empty("NoCost", "EPSG:4326", {"name": "text"})
nodep.add_feature("A", "Point", [30.0, 40.0], {"name": "A"})
nocost = engine.compute_resource_suitability(nodep, transport_layers=[lines],
                                             cost_layer=None, cost_weight=0.7)
assert nocost["best_score"] == 1.0
assert "network_reach_cost" not in nodep.schema
assert "network_reach_score" not in nodep.schema
print("compute_resource_suitability (reach, no cost surface) OK")

# multi-source Dijkstra accepts a list of source cells.
mdist, _ = engine._dijkstra_from(cost_surf.bands[0], [(15, 5), (15, 15)], False)
assert mdist[15, 5] == 0.0 and mdist[15, 15] == 0.0
assert mdist[15, 10] == 5.0
assert mdist[0, 10] == 20.0
print("_dijkstra_from (multi-source) OK")

# --- org influence zones: buffers ---
org = MapLayer.new_empty("Orgs", "EPSG:4326", {"name": "text"})
for name, lon, lat in (("Org1", 30.0, 40.0), ("Org2", 31.0, 40.0),
                       ("Org3", 32.0, 40.0)):
    org.add_feature(name, "Point", [lon, lat], {"name": name})
bres = engine.generate_org_influence_zones(org, method="buffers",
                                           buffer_rings_km=(10.0, 50.0, 100.0))
assert bres["orgs_processed"] == 3
assert bres["polygons_layer"] is not None
assert "nearest_org_km" in org.schema and "org_control_zone" in org.schema
near0 = org.features[0].properties["nearest_org_km"]
assert abs(near0 - 85.3) < 1.0
assert org.features[0].properties["org_control_zone"] == "Zone 3 (< 100 km)"
rings = bres["polygons_layer"]
assert len(rings.features) == 9
assert {f.properties["ring_km"] for f in rings.features} == {10.0, 50.0, 100.0}
assert all(f.geometry["type"] == "Polygon" for f in rings.features)
assert "Zone 3 (< 100 km)" in bres["control_zone_counts"]
print("generate_org_influence_zones (buffers) OK")

# --- org influence zones: voronoi ---
vres = engine.generate_org_influence_zones(org, method="voronoi")
vcells = vres["polygons_layer"]
assert vcells is not None and len(vcells.features) == 3
assert "area_km2" in vcells.schema
assert all(f.properties["area_km2"] > 0.0 for f in vcells.features)
print("generate_org_influence_zones (voronoi) OK")

# --- org influence zones: density ---
dres = engine.generate_org_influence_zones(org, method="density", density_cells=48)
grid = dres["density_grid"]
assert isinstance(grid, np.ndarray) and grid.shape == (48, 48)
extent = dres["density_extent"]
assert extent is not None and len(extent) == 4
assert grid.max() >= 1.0
print("generate_org_influence_zones (density) OK")

# --- org influence zones: empty layer ---
empty_org = MapLayer.new_empty("None", "EPSG:4326", {"name": "text"})
eres = engine.generate_org_influence_zones(empty_org, method="buffers")
assert eres["orgs_processed"] == 0
assert eres["polygons_layer"] is None and eres["density_grid"] is None
print("generate_org_influence_zones (empty) OK")

# --- corridor vulnerability ---
corrs = MapLayer.new_empty("Routes", "EPSG:4326", {"name": "text"})
corrs.add_feature("Road A", "LineString", [[30.0, 39.9], [30.5, 39.9]],
                  {"name": "Road A"})
corrs.add_feature("Road B", "LineString", [[30.0, 42.0], [31.0, 42.0]],
                  {"name": "Road B"})
vres = engine.calculate_corridor_vulnerability(corrs, org, risk_radius_km=25.0)
assert vres["total"] == 2 and vres["vulnerable"] == 1
assert vres["vulnerable_names"] == ["Road A"]
assert "corridor_risk_km" in corrs.schema
assert "nearest_org_name" in corrs.schema
assert "corridor_vulnerable" in corrs.schema
ra, rb = corrs.features
assert ra.properties["corridor_vulnerable"] is True
assert abs(ra.properties["corridor_risk_km"] - 11.13) < 0.5
assert ra.properties["nearest_org_name"] == "Org1"
assert rb.properties["corridor_vulnerable"] is False
assert rb.properties["corridor_risk_km"] is not None
ev = engine.calculate_corridor_vulnerability(
    MapLayer.new_empty("E", "EPSG:4326", {"name": "text"}), org)
assert ev["total"] == 0 and ev["vulnerable"] == 0
print("calculate_corridor_vulnerability OK")

# --- SuitabilityAnalysisDialog get_data ---
sdlg = main.SuitabilityAnalysisDialog(frame, [deposits, lines], [flat])
assert sdlg.get_data() == (deposits, None, None, 50.0, None, None, 1, 0.0)
sdlg.corridors_ctrl.SetSelection(2)
sdlg.terrain_ctrl.SetSelection(1)
sdlg.maxdist_ctrl.SetValue("35")
sdlg.slope_ctrl.SetValue("8")
assert sdlg.get_data() == (deposits, lines, flat, 35.0, 8.0, None, 1, 0.0)
sdlg.cost_ctrl.SetSelection(1)
sdlg.weight_ctrl.SetValue("0.6")
assert sdlg.get_data()[:3] == (deposits, lines, flat)
assert sdlg.get_data()[3] == 35.0 and sdlg.get_data()[4] == 8.0
assert sdlg.get_data()[5] is flat and sdlg.get_data()[6] == 1
assert sdlg.get_data()[7] == 0.6
sdlg.weight_ctrl.SetValue("junk")
assert sdlg.get_data()[7] == 0.0
sdlg.maxdist_ctrl.SetValue("abc")
sdlg.slope_ctrl.SetValue("x")
d = sdlg.get_data()
assert d[3] == 50.0 and d[4] is None and d[7] == 0.0
sdlg.Destroy()
print("SuitabilityAnalysisDialog get_data OK")

# --- OrgReachDialog get_data ---
odlg = main.OrgReachDialog(frame, [org, corrs])
assert odlg.get_data()[:2] == (org, "buffers")
odlg.method_ctrl.SetSelection(1)
odlg.rings_ctrl.SetValue("5, 15")
odlg.corridors_ctrl.SetSelection(2)
odlg.risk_ctrl.SetValue("30")
assert odlg.get_data() == (org, "voronoi", (5.0, 15.0), corrs, 30.0)
odlg.method_ctrl.SetSelection(2)
odlg.rings_ctrl.SetValue("junk")
odlg.risk_ctrl.SetValue("abc")
d = odlg.get_data()
assert d[1] == "density" and d[2] == (10.0, 50.0, 100.0) and d[4] == 25.0
odlg.Destroy()
print("OrgReachDialog get_data OK")

# --- network cost surfaces: least-cost path on a uniform cost surface ---
flat_cost = TerrainLayer.new_empty("FlatCost", "EPSG:4326", width=10, height=10,
                                   bounds=(0.0, 0.0, 1.0, 1.0))
flat_cost.fill(1.0)
path_res = engine.least_cost_path(flat_cost, (0.05, 0.95), (0.95, 0.95),
                                  diagonal=False)
assert path_res["success"] is True
assert path_res["cells"] == 10
assert path_res["cumulative_cost"] == 9.0
assert len(path_res["path"]) == 10
assert all(abs(pt[1] - 0.95) < 1e-9 for pt in path_res["path"])
assert path_res["distance_km"] > 0.0
print("least_cost_path (uniform cost) OK")

# --- least-cost path: barrier detours and unreachable cells ---
barrier = TerrainLayer.new_empty("Barrier", "EPSG:4326", width=10, height=10,
                                 bounds=(0.0, 0.0, 1.0, 1.0))
barrier.fill(1.0)
barrier.bands[0][:, 5] = np.nan
barrier.bands[0][8, 5] = 1.0
detour = engine.least_cost_path(barrier, (0.05, 0.95), (0.95, 0.95),
                                diagonal=False)
assert detour["success"] is True and detour["cells"] > 10
assert detour["cumulative_cost"] > 9.0
closed = TerrainLayer.new_empty("Closed", "EPSG:4326", width=10, height=10,
                                bounds=(0.0, 0.0, 1.0, 1.0))
closed.fill(1.0)
closed.bands[0][:, 5] = np.nan
no_path = engine.least_cost_path(closed, (0.05, 0.95), (0.95, 0.95))
assert no_path["success"] is False
assert "No traversable path" in (no_path["error"] or "")
outside = engine.least_cost_path(closed, (50.0, 50.0), (0.95, 0.95))
assert outside["success"] is False
no_cost = engine.least_cost_path(None, (0.05, 0.95), (0.95, 0.95))
assert no_cost["success"] is False
print("least_cost_path (barrier detour) OK")

# --- cost_accumulation (cost-distance raster) ---
accum = engine.cost_accumulation(flat_cost, (0.45, 0.55), diagonal=False)
assert accum is not None and accum.shape == (10, 10)
assert accum[4, 4] == 0.0
assert accum[0, 0] == 8.0
assert accum[9, 9] == 10.0
assert accum[0, 9] == accum[9, 0]
closed2 = TerrainLayer.new_empty("Closed2", "EPSG:4326", width=10, height=10,
                                 bounds=(0.0, 0.0, 1.0, 1.0))
closed2.fill(1.0)
closed2.bands[0][0, :] = np.nan
accum2 = engine.cost_accumulation(closed2, (0.45, 0.55), diagonal=False)
assert accum2 is not None
assert np.isinf(accum2[0, 0])
assert np.isfinite(accum2[9, 9])
print("cost_accumulation OK")

# --- least_cost_network: chain / star / tree on a flat cost surface ---
flat_net = TerrainLayer.new_empty("FlatNet", "EPSG:4326", width=10, height=10,
                                  bounds=(0.0, 0.0, 1.0, 1.0))
flat_net.fill(1.0)
pts3 = MapLayer.new_empty("NetPts", "EPSG:4326", {"name": "text"})
pts3.add_feature("P1", "Point", [0.05, 0.95], {"name": "P1"})
pts3.add_feature("P2", "Point", [0.95, 0.95], {"name": "P2"})
pts3.add_feature("P3", "Point", [0.95, 0.05], {"name": "P3"})
chain_layer, chain_summary = engine.least_cost_network(
    flat_net, pts3, mode="chain", diagonal=False)
assert chain_summary["paths"] == 2
assert abs(chain_summary["total_cost"] - 18.0) < 1e-9
assert chain_layer is not None and len(chain_layer.features) == 2
assert all(f.geometry["type"] == "LineString" for f in chain_layer.features)
assert "path_cost" in chain_layer.schema and "from_node" in chain_layer.schema
assert "path_distance_km" in chain_layer.schema
assert chain_layer.features[0].properties["from_node"] == "P1"
assert chain_layer.features[0].properties["to_node"] == "P2"
assert chain_layer.features[0].properties["cells"] == 10
star_layer, star_summary = engine.least_cost_network(
    flat_net, pts3, mode="star", diagonal=False)
assert star_summary["paths"] == 2
assert abs(star_summary["total_cost"] - 27.0) < 1e-9
assert all(f.properties["from_node"] == "P1" for f in star_layer.features)
tree_layer, tree_summary = engine.least_cost_network(
    flat_net, pts3, mode="tree", diagonal=False)
assert tree_summary["paths"] == 2 and tree_summary["nodes"] == 3
assert abs(tree_summary["total_cost"] - 18.0) < 1e-9
assert len(tree_layer.features) == 2
def_layer, def_summary = engine.least_cost_network(flat_net, pts3, diagonal=False)
assert def_summary["mode"] == "tree" and def_summary["paths"] == 2
empty_net, es = engine.least_cost_network(
    flat_net, MapLayer.new_empty("E", "EPSG:4326", {"name": "text"}))
assert es["nodes"] == 0 and empty_net is None
single = MapLayer.new_empty("S", "EPSG:4326", {"name": "text"})
single.add_feature("Only", "Point", [0.05, 0.95], {"name": "Only"})
_, ss = engine.least_cost_network(flat_net, single)
assert ss["nodes"] == 1 and ss["paths"] == 0
_, bad = engine.least_cost_network(None, pts3)
assert bad["nodes"] == 0
print("least_cost_network OK")

# --- _feature_lonlat helper ---
assert main._feature_lonlat(pts3, "P1") == (0.05, 0.95)
assert main._feature_lonlat(pts3, "P2") == (0.95, 0.95)
assert main._feature_lonlat(pts3, "Missing") is None
print("_feature_lonlat OK")

# --- NetworkCostDialog get_data ---
ndlg = main.NetworkCostDialog(frame, [pts3, lines], [flat_net])
assert ndlg.get_data()[0] is pts3
assert ndlg.get_data()[1] is flat_net
assert ndlg.get_data()[2] == 1
assert ndlg.get_data()[3] == "tree"
assert ndlg.get_data()[4] is not None and ndlg.get_data()[5] is not None
assert ndlg.get_data()[6] is True
ndlg.method_ctrl.SetSelection(0)
assert ndlg.get_data()[3] == "pair"
assert ndlg.get_data()[4] == "P1"
assert ndlg.get_data()[5] == "P2"
ndlg.diag_cb.SetValue(False)
assert ndlg.get_data()[6] is False
ndlg.method_ctrl.SetSelection(1)
assert ndlg.get_data()[3] == "chain"
ndlg.Destroy()
print("NetworkCostDialog get_data OK")

frame.Destroy()
print("ALL SPATIAL ANALYSIS TESTS PASSED")
