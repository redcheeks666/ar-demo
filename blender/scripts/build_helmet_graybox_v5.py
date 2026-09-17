"""Build the isolated AEGIS-R7 graybox v5 surface-craft revision.

The accepted v4 blend is opened as a read-only source contract.  V5 preserves
its 55 export node names, parents, pivots and measured feature coordinates,
while replacing the faceplate/eye craft and refining seams, lower trim, neck
entry and segmented spines.  Every invocation starts from v4; ``--iteration``
therefore selects a real, reproducible geometry state rather than accumulating
edits on a previous v5 file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path
from typing import Callable

import bmesh
import bpy
from mathutils import Vector
from mathutils.geometry import delaunay_2d_cdt


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import helmet_v5_config as cfg
import build_helmet_graybox_v3 as base
import build_helmet_graybox_v4 as v4


# The shared v3 helpers consult their module-level cfg at call time.
base.cfg = cfg


def parse_args() -> argparse.Namespace:
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--iteration", type=int, default=1)
    return parser.parse_args(arguments)


def mesh_hash(obj: bpy.types.Object) -> str:
    digest = hashlib.sha256()
    digest.update(obj.name.encode("utf-8"))
    for vertex in obj.data.vertices:
        digest.update(f"{vertex.co.x:.9f},{vertex.co.y:.9f},{vertex.co.z:.9f};".encode("ascii"))
    for polygon in obj.data.polygons:
        digest.update((",".join(str(index) for index in polygon.vertices) + ";").encode("ascii"))
    for modifier in obj.modifiers:
        digest.update(f"{modifier.name}:{modifier.type};".encode("utf-8"))
    return digest.hexdigest()


def point_in_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    x, y = point
    inside = False
    previous = polygon[-1]
    for current in polygon:
        x0, y0 = previous
        x1, y1 = current
        if (y0 > y) != (y1 > y):
            crossing = (x1 - x0) * (y - y0) / (y1 - y0) + x0
            if x < crossing:
                inside = not inside
        previous = current
    return inside


def signed_area_screen(polygon: list[tuple[float, float]]) -> float:
    # Screen v grows downward, so use an x/up plane for triangulation.
    return 0.5 * sum(
        u0 * (-v1) - u1 * (-v0)
        for (u0, v0), (u1, v1) in zip(polygon, polygon[1:] + polygon[:1])
    )


def horizontal_limits(polygon: list[tuple[float, float]], v: float) -> tuple[float, float]:
    intersections = []
    for (u0, v0), (u1, v1) in zip(polygon, polygon[1:] + polygon[:1]):
        if abs(v1 - v0) < 1.0e-10:
            continue
        if min(v0, v1) <= v < max(v0, v1):
            factor = (v - v0) / (v1 - v0)
            intersections.append(u0 + factor * (u1 - u0))
    if len(intersections) < 2:
        return min(point[0] for point in polygon), max(point[0] for point in polygon)
    return min(intersections), max(intersections)


def constrained_control_mesh(
    polygon_uv: list[tuple[float, float]],
    controls_uv: list[tuple[float, float]],
) -> tuple[list[tuple[float, float]], list[tuple[int, ...]], list[tuple[int, int]]]:
    polygon = list(polygon_uv)
    if signed_area_screen(polygon) < 0.0:
        polygon.reverse()
    boundary_count = len(polygon)
    coordinates = [Vector((u, -v)) for u, v in polygon]
    seen = {(round(u, 7), round(v, 7)) for u, v in polygon}
    for u, v in controls_uv:
        key = (round(u, 7), round(v, 7))
        if key in seen or not point_in_polygon((u, v), polygon):
            continue
        seen.add(key)
        coordinates.append(Vector((u, -v)))
    boundary_edges = [(index, (index + 1) % boundary_count) for index in range(boundary_count)]
    result = delaunay_2d_cdt(
        coordinates,
        boundary_edges,
        [tuple(range(boundary_count))],
        1,
        1.0e-7,
        False,
    )
    output_coordinates, _, output_faces = result[:3]
    uv = [(float(point.x), float(-point.y)) for point in output_coordinates]
    faces = [tuple(face) for face in output_faces if len(face) >= 3]
    edge_counts: dict[tuple[int, int], int] = {}
    for face in faces:
        for first, second in zip(face, face[1:] + face[:1]):
            edge = tuple(sorted((first, second)))
            edge_counts[edge] = edge_counts.get(edge, 0) + 1
    exposed = [edge for edge, count in edge_counts.items() if count == 1]
    return uv, faces, exposed


def constrained_control_mesh_with_holes(
    polygon_uv: list[tuple[float, float]],
    controls_uv: list[tuple[float, float]],
    holes_uv: list[list[tuple[float, float]]],
) -> tuple[list[tuple[float, float]], list[tuple[int, ...]], list[tuple[int, int]]]:
    """Triangulate the frozen outline while retaining explicit slot loops."""
    polygon = list(polygon_uv)
    if signed_area_screen(polygon) < 0.0:
        polygon.reverse()

    input_uv = list(polygon)
    constraint_edges = [
        (index, (index + 1) % len(polygon)) for index in range(len(polygon))
    ]
    for hole in holes_uv:
        start = len(input_uv)
        input_uv.extend(hole)
        constraint_edges.extend(
            (start + index, start + (index + 1) % len(hole))
            for index in range(len(hole))
        )

    seen = {(round(u, 7), round(v, 7)) for u, v in input_uv}
    for u, v_value in controls_uv:
        key = (round(u, 7), round(v_value, 7))
        if key in seen or not point_in_polygon((u, v_value), polygon):
            continue
        if any(point_in_polygon((u, v_value), hole) for hole in holes_uv):
            continue
        seen.add(key)
        input_uv.append((u, v_value))

    coordinates = [Vector((u, -v_value)) for u, v_value in input_uv]
    result = delaunay_2d_cdt(
        coordinates,
        constraint_edges,
        [tuple(range(len(polygon)))],
        1,
        1.0e-7,
        False,
    )
    output_coordinates, _, output_faces = result[:3]
    uv = [(float(point.x), float(-point.y)) for point in output_coordinates]
    faces: list[tuple[int, ...]] = []
    for face in output_faces:
        if len(face) < 3:
            continue
        centroid = (
            sum(uv[index][0] for index in face) / len(face),
            sum(uv[index][1] for index in face) / len(face),
        )
        if not point_in_polygon(centroid, polygon):
            continue
        if any(point_in_polygon(centroid, hole) for hole in holes_uv):
            continue
        faces.append(tuple(face))

    edge_counts: dict[tuple[int, int], int] = {}
    for face in faces:
        for first, second in zip(face, face[1:] + face[:1]):
            edge = tuple(sorted((first, second)))
            edge_counts[edge] = edge_counts.get(edge, 0) + 1
    exposed = [edge for edge, count in edge_counts.items() if count == 1]
    return uv, faces, exposed


def recalculate_normals(obj: bpy.types.Object) -> None:
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        bm.to_mesh(obj.data)
        obj.data.update()
    finally:
        bm.free()


def closed_mesh_health(obj: bpy.types.Object) -> dict:
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bm.normal_update()
        return {
            "vertices": len(bm.verts),
            "edges": len(bm.edges),
            "faces": len(bm.faces),
            "boundary_edges": sum(1 for edge in bm.edges if edge.is_boundary),
            "non_manifold_edges": sum(1 for edge in bm.edges if not edge.is_manifold),
            "signed_volume": float(bm.calc_volume(signed=True)),
        }
    finally:
        bm.free()


def ensure_positive_closed_volume(obj: bpy.types.Object) -> dict:
    health = closed_mesh_health(obj)
    if health["non_manifold_edges"]:
        raise RuntimeError(f"Non-manifold procedural solid {obj.name}: {health}")
    if health["signed_volume"] < 0.0:
        bm = bmesh.new()
        try:
            bm.from_mesh(obj.data)
            bmesh.ops.reverse_faces(bm, faces=list(bm.faces))
            bm.to_mesh(obj.data)
            obj.data.update()
        finally:
            bm.free()
        health = closed_mesh_health(obj)
    if health["signed_volume"] <= 0.0:
        raise RuntimeError(f"Zero-volume procedural solid {obj.name}: {health}")
    return health


def triangulate_for_boolean(obj: bpy.types.Object) -> None:
    """Give Exact Boolean an unambiguous planar tessellation."""
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bmesh.ops.triangulate(
            bm,
            faces=list(bm.faces),
            quad_method="BEAUTY",
            ngon_method="BEAUTY",
        )
        bm.to_mesh(obj.data)
        obj.data.update()
    finally:
        bm.free()


def delete_object(name: str) -> None:
    obj = bpy.data.objects.get(name)
    if obj is not None:
        bpy.data.objects.remove(obj, do_unlink=True)


def add_micro_bevel(obj: bpy.types.Object, width: float, angle_degrees: float = 15.0) -> None:
    bevel = obj.modifiers.get("V5 Consistent Micro Bevel")
    if bevel is None:
        bevel = obj.modifiers.new(name="V5 Consistent Micro Bevel", type="BEVEL")
    bevel.width = width
    bevel.segments = 2
    bevel.limit_method = "ANGLE"
    bevel.angle_limit = math.radians(angle_degrees)
    if hasattr(bevel, "harden_normals"):
        bevel.harden_normals = True


def assign_and_parent(
    obj: bpy.types.Object,
    material_key: str,
    materials: dict[str, bpy.types.Material],
    root: bpy.types.Object,
    pivot: Vector,
    owner: str | None = None,
) -> None:
    base.assign_part_materials(obj, material_key, materials)
    obj["aegis_v3_managed"] = True
    obj["aegis_v4_feature"] = True
    obj["aegis_v5_craft"] = True
    obj["aegis_version"] = 5
    if owner:
        obj["feature_owner"] = owner
    base.set_origin_preserve_world_geometry(obj, pivot)
    base.parent_to_root(obj, root)


def symmetric_faceplate_polygon(feature_data: dict) -> list[tuple[float, float]]:
    bbox = feature_data["views"]["front"]["helmet_silhouette_bbox_px"]
    records = feature_data["views"]["front"]["features"]["faceplate_outer_boundary"]["points"]
    polygon = [v4.front_normalized(tuple(record["source_px"]), bbox) for record in records]
    # Correspondences follow the measured closed outline.  Averaging magnitudes
    # preserves its accepted total width/height while enforcing true X mirror.
    pairs = ((0, 3), (1, 2), (4, 27), (5, 26), (6, 25), (7, 24), (8, 23),
             (9, 22), (10, 21), (11, 20), (12, 19), (13, 18), (14, 17), (15, 16))
    result = list(polygon)
    for first, second in pairs:
        left_index, right_index = (first, second) if polygon[first][0] < polygon[second][0] else (second, first)
        magnitude = 0.5 * (abs(polygon[left_index][0]) + abs(polygon[right_index][0]))
        v_average = 0.5 * (polygon[left_index][1] + polygon[right_index][1])
        result[left_index] = (-magnitude, v_average)
        result[right_index] = (magnitude, v_average)
    return result


def faceplate_controls(polygon: list[tuple[float, float]]) -> list[tuple[float, float]]:
    controls = []
    # Sparse structural rows make broad planar facets.  There is deliberately
    # no high-resolution displacement lattice.
    rows = (
        0.295, 0.330, 0.365, 0.400, 0.435, 0.470, 0.505,
        0.540, 0.575, 0.610, 0.645, 0.680, 0.715, 0.750,
    )
    fractions = (-0.78, -0.52, -0.27, 0.0, 0.27, 0.52, 0.78)
    for v_value in rows:
        lower, upper = horizontal_limits(polygon, v_value)
        half_width = min(abs(lower), abs(upper))
        for fraction in fractions:
            point = (half_width * fraction, v_value)
            if point_in_polygon(point, polygon):
                controls.append(point)
    return controls


def structured_faceplate_topology(
    polygon: list[tuple[float, float]],
) -> tuple[list[tuple[float, float]], list[tuple[int, ...]], list[tuple[int, int]]]:
    """Build a coarse ruled control cage with broad planar quad facets.

    The accepted measured V/bottom boundary remains explicit.  Only those two
    concave transition bands use triangles; the forehead, brow, nose, cheek,
    mouth and chin zones are large regular quads.
    """
    vertices: list[tuple[float, float]] = []
    lookup: dict[tuple[float, float], int] = {}
    faces: list[tuple[int, ...]] = []

    def mapped(point: tuple[float, float]) -> int:
        key = (round(point[0], 8), round(point[1], 8))
        if key not in lookup:
            lookup[key] = len(vertices)
            vertices.append(point)
        return lookup[key]

    def triangulate_region(region: list[tuple[float, float]]) -> None:
        coordinates = [Vector((u, -v)) for u, v in region]
        edges = [(index, (index + 1) % len(region)) for index in range(len(region))]
        result = delaunay_2d_cdt(
            coordinates, edges, [tuple(range(len(region)))], 1, 1.0e-7, False
        )
        output_coordinates, _, output_faces = result[:3]
        output_uv = [(float(point.x), float(-point.y)) for point in output_coordinates]
        output_map = [mapped(point) for point in output_uv]
        for face in output_faces:
            if len(face) >= 3:
                faces.append(tuple(output_map[index] for index in face))

    # Keep the measured concave forehead V and chin V as explicit boundary
    # bands, then use a uniformly spaced ruled cage through the visible skin.
    # The denser *control* rows are still deliberately coarse (well below a
    # displacement lattice) but make every Boolean-created triangle nearly
    # coplanar with its neighbour, removing the random triangular highlights
    # seen in iteration 3.
    row_values = (
        0.295, 0.330, 0.365, 0.400, 0.435, 0.470, 0.505,
        0.540, 0.575, 0.610, 0.645, 0.680, 0.715, 0.750,
    )
    fractions = (-1.0, -0.62, -0.26, 0.0, 0.26, 0.62, 1.0)
    row_indices: list[list[int]] = []
    row_points: list[list[tuple[float, float]]] = []
    for v_value in row_values:
        lower, upper = horizontal_limits(polygon, v_value)
        half_width = min(abs(lower), abs(upper))
        points = [(half_width * fraction, v_value) for fraction in fractions]
        row_points.append(points)
        row_indices.append([mapped(point) for point in points])
    for row_index in range(len(row_indices) - 1):
        lower_row = row_indices[row_index]
        upper_row = row_indices[row_index + 1]
        for column in range(len(fractions) - 1):
            faces.append(
                (
                    lower_row[column],
                    lower_row[column + 1],
                    upper_row[column + 1],
                    upper_row[column],
                )
            )

    top_path = [polygon[index] for index in (26, 27, 0, 1, 2, 3, 4, 5)]
    top_region = [row_points[0][0], *top_path, row_points[0][-1], *reversed(row_points[0][1:-1])]
    triangulate_region(top_region)
    bottom_path = [polygon[index] for index in (12, 13, 14, 15, 16, 17, 18, 19)]
    bottom_region = [*row_points[-1], *bottom_path]
    triangulate_region(bottom_region)

    edge_counts: dict[tuple[int, int], int] = {}
    for face in faces:
        for first, second in zip(face, face[1:] + face[:1]):
            edge = tuple(sorted((first, second)))
            edge_counts[edge] = edge_counts.get(edge, 0) + 1
    exposed = [edge for edge, count in edge_counts.items() if count == 1]
    return vertices, faces, exposed


def interpolate_profile(rows: tuple[tuple[float, tuple[float, float, float]], ...], v: float) -> tuple[float, float, float]:
    if v <= rows[0][0]:
        return rows[0][1]
    for (v0, values0), (v1, values1) in zip(rows, rows[1:]):
        if v <= v1:
            factor = (v - v0) / max(1.0e-8, v1 - v0)
            return tuple(a * (1.0 - factor) + b * factor for a, b in zip(values0, values1))
    return rows[-1][1]


def machined_faceplate_offset(
    u: float,
    v_value: float,
    polygon: list[tuple[float, float]],
    tuning: dict,
) -> float:
    # Values are (center ridge, cheek facet, boundary seat) in metres.  Linear
    # interpolation over the sparse control mesh yields planar faces and clean
    # deliberate breaks at the brow/nose/mouth/chin rows.
    rows = (
        (0.190, (0.0028, 0.0025, 0.0004)),
        (0.260, (0.0040, 0.0034, 0.0001)),
        (0.330, (0.0056, 0.0043, -0.0002)),
        (0.400, (0.0072, 0.0052, -0.0005)),
        (0.470, (0.0080, 0.0046, -0.0008)),
        (0.540, (0.0084, 0.0040, -0.0010)),
        (0.600, (0.0100, 0.0070, -0.0011)),
        (0.655, (0.0080, 0.0060, -0.0010)),
        (0.710, (0.0060, 0.0045, -0.0007)),
        (0.760, (0.0042, 0.0031, -0.0003)),
        (0.800, (0.0048, 0.0035, 0.0000)),
    )
    center, cheek, edge = interpolate_profile(rows, v_value)
    lower, upper = horizontal_limits(polygon, v_value)
    half_width = max(0.001, min(abs(lower), abs(upper)))
    ratio = min(1.0, abs(u) / half_width)
    if ratio <= 0.36:
        lateral = ratio / 0.36
        depth = center * (1.0 - lateral) + cheek * lateral
    else:
        lateral = (ratio - 0.36) / 0.64
        depth = cheek * (1.0 - lateral) + edge * lateral
    # Seat only the lateral rim beneath the black bezel.  X/Z coordinates stay
    # on the accepted measured outline, so front feature validation is stable.
    edge_weight = max(0.0, min(1.0, (ratio - 0.72) / 0.28))
    edge_weight = edge_weight * edge_weight * (3.0 - 2.0 * edge_weight)
    return (
        depth * tuning["facet_depth_scale"]
        + tuning.get("faceplate_global_offset_m", 0.0)
        - tuning["side_edge_inset_m"] * edge_weight
    )


def create_projected_shell(
    name: str,
    polygon_uv: list[tuple[float, float]],
    controls_uv: list[tuple[float, float]],
    collection: bpy.types.Collection,
    project: Callable[[float, float, float], tuple[Vector, Vector]],
    offset_function: Callable[[float, float], float],
    thickness: float,
    flat_outer: bool = True,
) -> bpy.types.Object:
    uv, surface_faces, boundary_edges = constrained_control_mesh(polygon_uv, controls_uv)
    outer: list[Vector] = []
    inner: list[Vector] = []
    for u, v_value in uv:
        point, normal = project(u, v_value, offset_function(u, v_value))
        outer.append(point)
        inner.append(point - normal * thickness)
    count = len(outer)
    faces = list(surface_faces)
    faces.extend(tuple(reversed(tuple(index + count for index in face))) for face in surface_faces)
    for first, second in boundary_edges:
        faces.append((first, second, second + count, first + count))
    obj = base.create_mesh_object(name, outer + inner, faces, collection)
    for index, polygon in enumerate(obj.data.polygons):
        polygon.use_smooth = not flat_outer if index < len(surface_faces) else False
    recalculate_normals(obj)
    return obj


def create_structured_faceplate_shell(
    polygon_uv: list[tuple[float, float]],
    collection: bpy.types.Collection,
    project: Callable[[float, float, float], tuple[Vector, Vector]],
    offset_function: Callable[[float, float], float],
    thickness: float,
) -> bpy.types.Object:
    uv, surface_faces, boundary_edges = structured_faceplate_topology(polygon_uv)
    outer: list[Vector] = []
    inner: list[Vector] = []
    for u, v_value in uv:
        point, normal = project(u, v_value, offset_function(u, v_value))
        outer.append(point)
        inner.append(point - normal * thickness)
    count = len(outer)
    faces = list(surface_faces)
    faces.extend(tuple(reversed(tuple(index + count for index in face))) for face in surface_faces)
    for first, second in boundary_edges:
        faces.append((first, second, second + count, first + count))
    obj = base.create_mesh_object("Faceplate", outer + inner, faces, collection)
    surface_count = len(surface_faces)
    for index, polygon in enumerate(obj.data.polygons):
        # The sparse cage defines the deliberate brow/nose/cheek breaks in
        # geometry.  Smooth only the two broad skins so Blender does not expose
        # each internal quad triangulation as a random triangular highlight;
        # keep the explicit thickness walls hard.
        polygon.use_smooth = index < surface_count * 2
    recalculate_normals(obj)
    return obj


def create_topological_faceplate_shell(
    polygon_uv: list[tuple[float, float]],
    eye_slots_uv: list[list[tuple[float, float]]],
    collection: bpy.types.Collection,
    project: Callable[[float, float, float], tuple[Vector, Vector]],
    offset_function: Callable[[float, float], float],
    thickness: float,
) -> bpy.types.Object:
    """Build the faceplate with two explicit capsule openings and slot walls."""
    uv, surface_faces, exposed_edges = constrained_control_mesh_with_holes(
        polygon_uv,
        faceplate_controls(polygon_uv),
        eye_slots_uv,
    )
    outer: list[Vector] = []
    inner: list[Vector] = []
    for u, v_value in uv:
        point, normal = project(u, v_value, offset_function(u, v_value))
        outer.append(point)
        inner.append(point - normal * thickness)
    count = len(outer)
    faces = list(surface_faces)
    faces.extend(
        tuple(reversed(tuple(index + count for index in face)))
        for face in surface_faces
    )
    for first, second in exposed_edges:
        faces.append((first, second, second + count, first + count))
    obj = base.create_mesh_object("Faceplate", outer + inner, faces, collection)
    surface_count = len(surface_faces)
    for index, polygon in enumerate(obj.data.polygons):
        polygon.use_smooth = index < surface_count * 2
    recalculate_normals(obj)
    return obj


def capsule_uv(
    center: tuple[float, float],
    length: float,
    height: float,
    angle_degrees: float,
    arc_steps: int = 6,
) -> list[tuple[float, float]]:
    radius = height * 0.5
    straight = max(0.0, length * 0.5 - radius)
    local = []
    for index in range(arc_steps + 1):
        angle = -math.pi * 0.5 + math.pi * index / arc_steps
        local.append((straight + radius * math.cos(angle), radius * math.sin(angle)))
    for index in range(arc_steps + 1):
        angle = math.pi * 0.5 + math.pi * index / arc_steps
        local.append((-straight + radius * math.cos(angle), radius * math.sin(angle)))
    rotation = math.radians(angle_degrees)
    cosine = math.cos(rotation)
    sine = math.sin(rotation)
    return [
        (center[0] + x * cosine - y * sine, center[1] + x * sine + y * cosine)
        for x, y in local
    ]


def create_projected_capsule_solid(
    name: str,
    polygon_uv: list[tuple[float, float]],
    collection: bpy.types.Collection,
    projector: v4.MasterSurfaceProjector,
    faceplate_offset: Callable[[float, float], float],
    recess: float,
    thickness: float,
) -> bpy.types.Object:
    outer = []
    inner = []
    for u, v_value in polygon_uv:
        surface_offset = faceplate_offset(u, v_value)
        point, normal = projector.front(u, v_value, surface_offset - recess)
        outer.append(point)
        inner.append(point - normal * thickness)
    count = len(outer)
    faces: list[tuple[int, ...]] = [tuple(range(count)), tuple(reversed(range(count, count * 2)))]
    for index in range(count):
        following = (index + 1) % count
        faces.append((index, following, count + following, count + index))
    obj = base.create_mesh_object(name, outer + inner, faces, collection)
    for polygon in obj.data.polygons:
        polygon.use_smooth = False
    recalculate_normals(obj)
    return obj


def create_front_planar_prism(
    name: str,
    polygon_uv: list[tuple[float, float]],
    collection: bpy.types.Collection,
    projector: v4.MasterSurfaceProjector,
    offset: float,
    thickness: float,
    seat_fraction: float = 0.0,
) -> bpy.types.Object:
    """Create one clean front plane plus a closed thickness wall.

    The projected X/Z outline remains tied to the accepted v4 front crop.
    Only Y is levelled, which eliminates the non-coplanar Delaunay fans that
    produced the iteration-3 X-shaped chin and jaw highlights.
    """
    projected = [projector.front(u, v_value, offset)[0] for u, v_value in polygon_uv]
    minimum_y = min(point.y for point in projected)
    maximum_y = max(point.y for point in projected)
    seat_fraction = max(0.0, min(1.0, seat_fraction))
    front_y = minimum_y + (maximum_y - minimum_y) * seat_fraction
    outer = [Vector((point.x, front_y, point.z)) for point in projected]
    inner = [Vector((point.x, front_y + thickness, point.z)) for point in projected]
    count = len(outer)
    faces: list[tuple[int, ...]] = [
        tuple(range(count)),
        tuple(reversed(range(count, count * 2))),
    ]
    for index in range(count):
        following = (index + 1) % count
        faces.append((index, following, count + following, count + index))
    obj = base.create_mesh_object(name, outer + inner, faces, collection)
    for polygon in obj.data.polygons:
        polygon.use_smooth = False
    recalculate_normals(obj)
    ensure_positive_closed_volume(obj)
    return obj


def create_slot_cutter(
    name: str,
    polygon_uv: list[tuple[float, float]],
    collection: bpy.types.Collection,
    projector: v4.MasterSurfaceProjector,
    faceplate_offset: Callable[[float, float], float],
) -> bpy.types.Object:
    # A straight world-Y capsule prism is intentionally used here.  A cutter
    # made by extruding separately varying surface normals can twist into a
    # self-enclosing volume on a faceted plate and make Exact Boolean remove
    # the wrong side.  X/Z still come from the frozen measured eye UVs.
    front = []
    rear = []
    eye_surface_y = [
        projector.front(u, v_value, faceplate_offset(u, v_value))[0].y
        for u, v_value in polygon_uv
    ]
    # Limit the cutter to the local faceplate thickness.  The former master-
    # envelope-long prism made Blender's manifold classifier create remote
    # nose/chin holes even though the mesh stayed formally closed.
    front_y = min(eye_surface_y) - 0.008
    rear_y = max(eye_surface_y) + 0.010
    for u, v_value in polygon_uv:
        x = u * projector.model.height
        z = projector.model.top_z - v_value * projector.model.height
        front.append(Vector((x, front_y, z)))
        rear.append(Vector((x, rear_y, z)))
    count = len(front)
    faces: list[tuple[int, ...]] = [tuple(range(count)), tuple(reversed(range(count, count * 2)))]
    for index in range(count):
        following = (index + 1) % count
        faces.append((index, following, count + following, count + index))
    cutter = base.create_mesh_object(name, front + rear, faces, collection)
    recalculate_normals(cutter)
    health = ensure_positive_closed_volume(cutter)
    print(f"AEGIS_V5_CUTTER_HEALTH name={name} health={health}")
    cutter.hide_render = True
    cutter.display_type = "WIRE"
    return cutter


def apply_boolean_difference(target: bpy.types.Object, cutter: bpy.types.Object, label: str) -> None:
    before_topology = (len(target.data.vertices), len(target.data.edges), len(target.data.polygons))
    bpy.ops.object.select_all(action="DESELECT")
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    modifier = target.modifiers.new(name=f"V5 Real Eye Slot {label}", type="BOOLEAN")
    modifier.operation = "DIFFERENCE"
    # FLOAT is stable for this short local prism.  EXACT misclassifies the
    # thin ruled shell as enclosed, while MANIFOLD created remote nose holes
    # in iterations 3/4.
    modifier.solver = "FLOAT"
    modifier.object = cutter
    if hasattr(modifier, "use_self"):
        modifier.use_self = False
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    after_topology = (len(target.data.vertices), len(target.data.edges), len(target.data.polygons))
    if after_topology == before_topology:
        raise RuntimeError(f"Boolean eye slot {label} did not alter Faceplate topology")
    print(f"AEGIS_V5_SLOT_TOPOLOGY side={label} before={before_topology} after={after_topology}")
    recalculate_normals(target)


def create_bezel_ring(
    polygon_uv: list[tuple[float, float]],
    collection: bpy.types.Collection,
    projector: v4.MasterSurfaceProjector,
    model: base.EnvelopeModel,
    tuning: dict,
    faceplate_offset: Callable[[float, float], float],
) -> bpy.types.Object:
    width_norm = tuning["bezel_width_m"] / model.height
    outer_uv = []
    for u, v_value in polygon_uv:
        direction = Vector((u, v_value - 0.49))
        if direction.length < 1.0e-8:
            direction = Vector((0.0, 1.0))
        direction.normalize()
        local_width = width_norm * (tuning["mouth_bezel_scale"] if v_value > 0.735 else 1.0)
        outer_uv.append((u + direction.x * local_width, v_value + direction.y * local_width))

    front_inner: list[Vector] = []
    front_outer: list[Vector] = []
    back_inner: list[Vector] = []
    back_outer: list[Vector] = []
    bezel_offset = tuning.get("bezel_projection_offset_m", 0.0028)
    for inner_point, outer_point in zip(polygon_uv, outer_uv):
        inner_location, inner_normal = projector.front(*inner_point, bezel_offset)
        outer_location, outer_normal = projector.front(*outer_point, bezel_offset)
        front_inner.append(inner_location)
        front_outer.append(outer_location)
        back_inner.append(inner_location - inner_normal * 0.0015)
        back_outer.append(outer_location - outer_normal * 0.0015)
    count = len(polygon_uv)
    vertices = front_inner + front_outer + back_inner + back_outer
    faces = []
    for index in range(count):
        following = (index + 1) % count
        # Front/back ribbon, then inner/outer thickness walls.
        faces.extend(
            (
                (index, following, count + following, count + index),
                (2 * count + index, 3 * count + index, 3 * count + following, 2 * count + following),
                (index, 2 * count + index, 2 * count + following, following),
                (count + index, count + following, 3 * count + following, 3 * count + index),
            )
        )

    def append_rail(points: list[Vector], width: float, depth: float) -> None:
        start = len(vertices)
        for index, point in enumerate(points):
            previous = points[max(0, index - 1)]
            following = points[min(len(points) - 1, index + 1)]
            tangent = (following - previous).normalized()
            side_axis = tangent.cross(Vector((0.0, 1.0, 0.0)))
            if side_axis.length < 1.0e-8:
                side_axis = Vector((1.0, 0.0, 0.0))
            side_axis.normalize()
            depth_axis = side_axis.cross(tangent).normalized()
            vertices.extend(
                (
                    point + side_axis * (width * 0.5) + depth_axis * (depth * 0.5),
                    point - side_axis * (width * 0.5) + depth_axis * (depth * 0.5),
                    point - side_axis * (width * 0.5) - depth_axis * (depth * 0.5),
                    point + side_axis * (width * 0.5) - depth_axis * (depth * 0.5),
                )
            )
        faces.append(tuple(reversed(tuple(start + corner for corner in range(4)))))
        for index in range(len(points) - 1):
            current = start + index * 4
            following = current + 4
            for corner in range(4):
                next_corner = (corner + 1) % 4
                faces.append(
                    (
                        current + corner,
                        current + next_corner,
                        following + next_corner,
                        following + corner,
                    )
                )
        end = start + (len(points) - 1) * 4
        faces.append(tuple(end + corner for corner in range(4)))

    def append_side_blade(
        points: list[Vector],
        x_center: float,
        thickness: float,
        return_depth: float,
    ) -> None:
        """Add a closed YZ return whose front projection is edge-on.

        The blade is physically present in strict side views, but its narrow
        X thickness sits beneath the accepted red/black frame in front view.
        This covers the gold depth contour without adding a visible faceplate
        feature or the floating cable silhouette produced by round rails.
        """
        start = len(vertices)
        half_thickness = thickness * 0.5
        for point in points:
            vertices.extend(
                (
                    Vector((x_center - half_thickness, point.y, point.z)),
                    Vector((x_center + half_thickness, point.y, point.z)),
                    Vector((x_center + half_thickness, point.y + return_depth, point.z)),
                    Vector((x_center - half_thickness, point.y + return_depth, point.z)),
                )
            )
        faces.append(tuple(reversed(tuple(start + corner for corner in range(4)))))
        for index in range(len(points) - 1):
            current = start + index * 4
            following = current + 4
            for corner in range(4):
                next_corner = (corner + 1) % 4
                faces.append(
                    (
                        current + corner,
                        current + next_corner,
                        following + next_corner,
                        following + corner,
                    )
                )
        end = start + (len(points) - 1) * 4
        faces.append(tuple(end + corner for corner in range(4)))

    if tuning.get("side_guard_rails", False):
        # Iteration-8 experiment retained for reproducibility.  The final
        # craft uses the integrated projected bezel instead of floating rails.
        for sign in (-1.0, 1.0):
            rail_points = []
            for index in range(25):
                v_value = 0.198 + (0.792 - 0.198) * index / 24.0
                lower, upper = horizontal_limits(polygon_uv, v_value)
                half_width = min(abs(lower), abs(upper))
                u = sign * half_width
                boundary, _ = projector.front(u, v_value, faceplate_offset(u, v_value))
                sample_y = []
                for fraction in (-0.75, -0.50, -0.25, 0.0, 0.25, 0.50, 0.75):
                    sample_u = half_width * fraction
                    sample, _ = projector.front(
                        sample_u,
                        v_value,
                        faceplate_offset(sample_u, v_value),
                    )
                    sample_y.append(sample.y)
                boundary.y = min(sample_y) - tuning.get("side_guard_forward_m", 0.0060)
                rail_points.append(boundary)
            append_rail(
                rail_points,
                width=tuning.get("side_guard_width_m", 0.0032),
                depth=tuning.get("side_guard_depth_m", 0.0030),
            )

    if tuning.get("side_occlusion_blades", False):
        for sign in (-1.0, 1.0):
            blade_points = []
            for index in range(33):
                start_v = tuning.get("side_blade_v_start", 0.198)
                end_v = tuning.get("side_blade_v_end", 0.792)
                v_value = start_v + (end_v - start_v) * index / 32.0
                lower, upper = horizontal_limits(polygon_uv, v_value)
                half_width = min(abs(lower), abs(upper))
                sample_y = []
                for fraction in (-0.75, -0.50, -0.25, 0.0, 0.25, 0.50, 0.75):
                    sample_u = half_width * fraction
                    sample, _ = projector.front(
                        sample_u,
                        v_value,
                        faceplate_offset(sample_u, v_value),
                    )
                    sample_y.append(sample.y)
                blade_points.append(
                    Vector(
                        (
                            sign * tuning.get("side_blade_x_m", 0.075),
                            min(sample_y) - tuning.get("side_blade_forward_m", 0.00005),
                            model.top_z - v_value * model.height,
                        )
                    )
                )
            append_side_blade(
                blade_points,
                sign * tuning.get("side_blade_x_m", 0.075),
                tuning.get("side_blade_thickness_m", 0.00055),
                tuning.get("side_blade_return_depth_m", 0.0022),
            )

    if tuning.get("center_bezel_spine", False):
        center_points = []
        for index in range(41):
            v_value = 0.195 + (0.794 - 0.195) * index / 40.0
            point, _ = projector.front(
                0.0,
                v_value,
                faceplate_offset(0.0, v_value) + tuning.get("center_spine_offset_m", 0.00055),
            )
            point.x = 0.0
            point.z = model.top_z - v_value * model.height
            center_points.append(point)
        append_rail(
            center_points,
            width=tuning.get("center_spine_width_m", 0.0020),
            depth=tuning.get("center_spine_depth_m", 0.0012),
        )

    obj = base.create_mesh_object("FaceplateBezel", vertices, faces, collection)
    for polygon in obj.data.polygons:
        polygon.use_smooth = False
    recalculate_normals(obj)
    obj["v5_closed_side_guard_rails"] = bool(tuning.get("side_guard_rails", False))
    obj["v5_edge_on_side_occlusion_blades"] = bool(tuning.get("side_occlusion_blades", False))
    obj["v5_center_bezel_spine"] = bool(tuning.get("center_bezel_spine", False))
    return obj


def world_vertices(obj: bpy.types.Object) -> list[Vector]:
    return [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]


def transform_world_vertices(obj: bpy.types.Object, transform: Callable[[Vector], Vector]) -> None:
    inverse = obj.matrix_world.inverted()
    for vertex in obj.data.vertices:
        vertex.co = inverse @ transform(obj.matrix_world @ vertex.co)
    obj.data.update()


def scale_about_world_bbox(obj: bpy.types.Object, factors: tuple[float, float, float]) -> None:
    points = world_vertices(obj)
    minimum = Vector(tuple(min(point[axis] for point in points) for axis in range(3)))
    maximum = Vector(tuple(max(point[axis] for point in points) for axis in range(3)))
    center = (minimum + maximum) * 0.5
    scale = Vector(factors)
    transform_world_vertices(obj, lambda point: center + Vector(tuple((point[axis] - center[axis]) * scale[axis] for axis in range(3))))


def mirror_left_to_right(
    left_name: str,
    right_name: str,
    collection: bpy.types.Collection,
    materials: dict[str, bpy.types.Material],
    root: bpy.types.Object,
    pivot: Vector,
    owner: str | None,
) -> bpy.types.Object:
    left = bpy.data.objects[left_name]
    vertices = [Vector((-point.x, point.y, point.z)) for point in world_vertices(left)]
    faces = [tuple(reversed(tuple(polygon.vertices))) for polygon in left.data.polygons]
    smooth = [polygon.use_smooth for polygon in left.data.polygons]
    delete_object(right_name)
    right = base.create_mesh_object(right_name, vertices, faces, collection)
    for polygon, use_smooth in zip(right.data.polygons, smooth):
        polygon.use_smooth = use_smooth
    assign_and_parent(right, right_name, materials, root, pivot, owner)
    recalculate_normals(right)
    return right


def create_straight_rear_spine(
    collection: bpy.types.Collection,
    projector: v4.MasterSurfaceProjector,
    model: base.EnvelopeModel,
    tuning: dict,
) -> bpy.types.Object:
    half_width = (cfg.FIT["spine_width"] * 0.55) / model.height
    center_v = tuning.get("rear_spine_08_center_v", 0.790)
    half_span = tuning.get("rear_spine_08_v_span", 0.150) * 0.5
    polygon = [
        (-half_width, center_v - half_span),
        (half_width, center_v - half_span),
        (half_width, center_v + half_span),
        (-half_width, center_v + half_span),
    ]
    return create_projected_shell(
        "RearSpine_08",
        polygon,
        [],
        collection,
        projector.back,
        lambda _u, _v: 0.0180,
        0.0018,
        flat_outer=True,
    )


def extend_crown_seam(tuning: dict) -> None:
    # Leave the accepted crown meshes unchanged.  Stretching their last open
    # control row makes the Solidify/Bevel result non-manifold.  Instead extend
    # the lower red shells upward beneath the crown, leaving only a narrow dark
    # reveal while maintaining a continuous outer red surface.
    for name in ("RearShell_L", "RearShell_R", "Temple_L", "Temple_R"):
        obj = bpy.data.objects[name]
        points = world_vertices(obj)
        maximum_z = max(point.z for point in points)
        transition_band = 0.014
        raise_amount = tuning["crown_seam_close_m"] + tuning["lower_shell_raise_m"]
        inverse = obj.matrix_world.inverted()
        for vertex in obj.data.vertices:
            world = obj.matrix_world @ vertex.co
            distance = maximum_z - world.z
            if distance <= transition_band:
                weight = max(0.0, min(1.0, 1.0 - distance / transition_band))
                weight = weight * weight * (3.0 - 2.0 * weight)
                world.z += raise_amount * weight
                vertex.co = inverse @ world
        obj.data.update()


def inset_neck_ring(tuning: dict) -> None:
    scale = tuning["neck_xy_scale"]
    raise_z = tuning["neck_raise_m"]
    for name in ("NeckRingFront", "NeckRingRear"):
        obj = bpy.data.objects[name]
        transform_world_vertices(obj, lambda point: Vector((point.x * scale, point.y * scale, point.z + raise_z)))
        obj["v5_neck_inset_scale"] = scale
        obj["v5_neck_raise_m"] = raise_z


def tuck_inner_shell_lower_rim(tuning: dict) -> None:
    """Hide the dark construction rim behind the red lower armor.

    Iteration 3 proved that the apparent stand foot was not only the named
    NeckRing: the lowest InnerShell row extended another 18 mm below it.  A
    smooth lower-band tuck preserves the upper shell and all pivots while
    lifting/insetting just that hidden construction rim.
    """
    obj = bpy.data.objects["InnerShell"]
    inverse = obj.matrix_world.inverted()
    band_top = -0.096
    band_height = 0.037
    maximum_raise = tuning.get("inner_rim_raise_m", 0.0225)
    maximum_inset = tuning.get("inner_rim_inset", 0.13)
    for vertex in obj.data.vertices:
        world = obj.matrix_world @ vertex.co
        if world.z >= band_top:
            continue
        weight = max(0.0, min(1.0, (band_top - world.z) / band_height))
        weight = weight * weight * (3.0 - 2.0 * weight)
        radial_scale = 1.0 - maximum_inset * weight
        world.x *= radial_scale
        world.y *= radial_scale
        world.z += maximum_raise * weight
        if "inner_rim_min_z_m" in tuning:
            world.z = max(world.z, tuning["inner_rim_min_z_m"])
        vertex.co = inverse @ world
    obj.data.update()
    obj["v5_lower_rim_tucked"] = True


def tighten_mouth_and_lower_frame(tuning: dict) -> None:
    lower_trim = bpy.data.objects["LowerUTrim"]
    # Keep the frozen front X/Z feature coordinates; only seat the dark trim
    # in depth.  Shrinking X/Z made iteration 3's mouth cavity read larger.
    scale_about_world_bbox(lower_trim, (1.0, 0.82, 1.0))
    # Seat dark trim slightly behind the red chin lip.
    transform_world_vertices(lower_trim, lambda point: Vector((point.x, point.y + 0.0012, point.z)))


def seat_faceplate_under_side_shells(faceplate: bpy.types.Object) -> None:
    """Extend existing red/black frame faces over the gold side depth.

    This is the integrated solution to the material-ID contour gate: the
    accepted X/Z feature layout is untouched, while the front faces of the
    existing Temple/Cheek/JawTrim nodes become a shallow return flange.  It
    avoids any floating helper rail and makes the gold skin genuinely sit
    beneath the red shell or black jawline in 3/4 and strict side views.
    """
    faceplate_front_y = min(point.y for point in world_vertices(faceplate))
    target_y = faceplate_front_y - 0.00015
    for name in (
        "Temple_L", "Temple_R",
        "Cheek_L", "Cheek_R",
        "JawTrim_L", "JawTrim_R",
    ):
        obj = bpy.data.objects[name]
        points = world_vertices(obj)
        minimum_y = min(point.y for point in points)
        if minimum_y <= target_y:
            obj["v5_faceplate_return_flange_y"] = minimum_y
            continue
        transition_band = 0.009
        inverse = obj.matrix_world.inverted()
        for vertex in obj.data.vertices:
            world = obj.matrix_world @ vertex.co
            weight = max(0.0, min(1.0, (minimum_y + transition_band - world.y) / transition_band))
            weight = weight * weight * (3.0 - 2.0 * weight)
            if weight > 0.0:
                world.y = world.y * (1.0 - weight) + target_y * weight
                vertex.co = inverse @ world
        obj.data.update()
        obj["v5_faceplate_return_flange_y"] = target_y



def refine_spines(
    tuning: dict,
    collection: bpy.types.Collection,
    materials: dict[str, bpy.types.Material],
    root: bpy.types.Object,
    pivots: dict[str, Vector],
    projector: v4.MasterSurfaceProjector,
    model: base.EnvelopeModel,
) -> None:
    def replace_bevel(obj: bpy.types.Object, width: float, angle_degrees: float) -> None:
        for modifier in list(obj.modifiers):
            if modifier.type == "BEVEL":
                obj.modifiers.remove(modifier)
        add_micro_bevel(obj, width, angle_degrees)

    scale = tuning["spine_scale"]
    crown_width_scale = tuning.get("crown_spine_width_scale", 0.96)
    crown_bevel = tuning.get("crown_spine_bevel_m", tuning["panel_bevel_m"] * 0.82)
    for name in ("CrownSpine_01", "CrownSpine_02", "CrownSpine_03", "CrownSpine_04"):
        obj = bpy.data.objects[name]
        scale_about_world_bbox(obj, (crown_width_scale, scale, scale))
        replace_bevel(obj, crown_bevel, 10.0)
    for index in range(1, 8):
        obj = bpy.data.objects[f"RearSpine_{index:02d}"]
        scale_about_world_bbox(obj, (0.96, 0.96, scale))
        replace_bevel(
            obj,
            tuning.get("rear_spine_bevel_m", tuning["panel_bevel_m"] * 0.78),
            10.0,
        )

    delete_object("RearSpine_08")
    last = create_straight_rear_spine(collection, projector, model, tuning)
    assign_and_parent(last, "RearSpine_08", materials, root, pivots["RearSpine_08"], "CrownRear")
    replace_bevel(
        last,
        tuning.get("rear_spine_bevel_m", tuning["panel_bevel_m"] * 0.78),
        10.0,
    )
    last["v5_straight_lower_spine"] = True

    cap = bpy.data.objects["CrownSpine_Cap"]
    scale_about_world_bbox(cap, tuple(tuning["cap_scale"]))
    replace_bevel(cap, tuning["panel_bevel_m"], 8.0)
    cap["v5_refined_segment_cap"] = True


def evaluated_triangle_count(objects: list[bpy.types.Object]) -> int:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    total = 0
    for obj in objects:
        if obj.type != "MESH":
            continue
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            mesh.calc_loop_triangles()
            total += len(mesh.loop_triangles)
        finally:
            evaluated.to_mesh_clear()
    return total


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    if args.iteration < 1:
        raise ValueError("iteration must be >= 1")
    cfg.assert_isolated_paths()
    if not cfg.V4_BLEND_SOURCE.is_file():
        raise FileNotFoundError(cfg.V4_BLEND_SOURCE)
    if not cfg.FEATURE_CURVES_PATH.is_file() or not cfg.MEASUREMENTS_PATH.is_file():
        raise FileNotFoundError("v4 feature curves and v3 measurements are required")

    tuning = cfg.craft_tuning(args.iteration)
    skip_micro_bevel_objects = set(tuning.get("skip_micro_bevel_objects", ()))
    bpy.ops.wm.open_mainfile(filepath=str(cfg.V4_BLEND_SOURCE))
    scene = bpy.context.scene
    scene.name = "AEGIS-R7 Graybox v5"
    root = bpy.data.objects.get("HelmetRoot")
    master = bpy.data.objects.get("HelmetEnvelope_Master")
    helmet_collection = bpy.data.collections.get(cfg.HELMET_COLLECTION_NAME)
    modeling_collection = bpy.data.collections.get("MODELING_V3")
    if root is None or master is None or helmet_collection is None or modeling_collection is None:
        raise RuntimeError("v4 source scene contract is incomplete")

    original_names = {obj.name for obj in helmet_collection.all_objects}
    expected_names = set(cfg.V4_NODE_NAMES)
    if original_names != expected_names:
        raise RuntimeError(
            f"v4 55-node contract mismatch: missing={sorted(expected_names - original_names)} "
            f"extra={sorted(original_names - expected_names)}"
        )
    pivots = {name: bpy.data.objects[name].matrix_world.translation.copy() for name in cfg.V4_NODE_NAMES}
    parents = {name: bpy.data.objects[name].parent.name if bpy.data.objects[name].parent else None for name in cfg.V4_NODE_NAMES}
    master_hash_before = mesh_hash(master)

    feature_data = json.loads(cfg.FEATURE_CURVES_PATH.read_text(encoding="utf-8"))
    measurements = json.loads(cfg.MEASUREMENTS_PATH.read_text(encoding="utf-8"))
    model = base.EnvelopeModel(measurements)
    projector = v4.MasterSurfaceProjector(master, model)
    materials = {
        name: bpy.data.materials.get(name) or base.make_material(name, specification)
        for name, specification in cfg.MATERIALS.items()
    }

    # --- Structured machined faceplate with explicit topological eye slots -------------
    faceplate_polygon = symmetric_faceplate_polygon(feature_data)
    faceplate_offset = lambda u, v_value: machined_faceplate_offset(u, v_value, faceplate_polygon, tuning)

    def faceplate_project(u: float, v_value: float, offset: float) -> tuple[Vector, Vector]:
        point, normal = projector.front(u, v_value, offset)
        # Freeze the accepted crop-space feature coordinates exactly.  Surface
        # craft changes depth only; normal offsets must not shrink X/Z width.
        point.x = u * model.height
        point.z = model.top_z - v_value * model.height
        return point, normal

    for name in ("Faceplate", "FaceplateBezel", "EyeHousing_L", "EyeHousing_R", "EyeLens_L", "EyeLens_R"):
        delete_object(name)

    eye_left = feature_data["views"]["front"]["features"]["eye_left_metrics"]
    eye_right = feature_data["views"]["front"]["features"]["eye_right_metrics"]
    eye_center_magnitude = 0.5 * (
        abs(float(eye_left["center_normalized_head_height"][0]))
        + abs(float(eye_right["center_normalized_head_height"][0]))
    )
    eye_center_v = 0.5 * (
        float(eye_left["center_normalized_head_height"][1])
        + float(eye_right["center_normalized_head_height"][1])
    )
    eye_length = 0.5 * (float(eye_left["length_head_height"]) + float(eye_right["length_head_height"]))
    eye_geometry = []
    for side, sign in (("L", -1.0), ("R", 1.0)):
        center = (sign * eye_center_magnitude, eye_center_v)
        angle = 3.2 if side == "L" else -3.2
        eye_geometry.append(
            {
                "side": side,
                "slot_uv": capsule_uv(center, eye_length + 0.012, 0.034, angle),
                "housing_uv": capsule_uv(center, eye_length + 0.008, 0.028, angle),
                "lens_uv": capsule_uv(center, eye_length, 0.0145, angle),
            }
        )

    faceplate = create_topological_faceplate_shell(
        faceplate_polygon,
        [record["slot_uv"] for record in eye_geometry],
        helmet_collection,
        faceplate_project,
        faceplate_offset,
        cfg.FIT["faceplate_thickness"],
    )
    assign_and_parent(faceplate, "Faceplate", materials, root, pivots["Faceplate"])
    faceplate["v5_surface_method"] = "ruled control cage with explicit capsule topology"
    faceplate["v5_feature_layout_source"] = str(cfg.FEATURE_CURVES_PATH)
    recalculate_normals(faceplate)
    faceplate_health = closed_mesh_health(faceplate)
    print(f"AEGIS_V5_FACEPLATE_TOPOLOGY={faceplate_health}")
    if faceplate_health["non_manifold_edges"] or faceplate_health["signed_volume"] <= 0.0:
        raise RuntimeError(f"Topological Faceplate is not a valid closed positive volume: {faceplate_health}")

    eye_records = []
    for record in eye_geometry:
        side = record["side"]
        housing = create_projected_capsule_solid(
            f"EyeHousing_{side}", record["housing_uv"], helmet_collection, projector, faceplate_offset,
            tuning["eye_housing_recess_m"], 0.0016,
        )
        lens = create_projected_capsule_solid(
            f"EyeLens_{side}", record["lens_uv"], helmet_collection, projector, faceplate_offset,
            tuning["eye_lens_recess_m"], 0.0010,
        )
        assign_and_parent(housing, f"EyeHousing_{side}", materials, root, pivots[f"EyeHousing_{side}"])
        assign_and_parent(lens, f"EyeLens_{side}", materials, root, pivots[f"EyeLens_{side}"])
        add_micro_bevel(housing, min(0.00030, tuning["panel_bevel_m"] * 0.50), 12.0)
        add_micro_bevel(lens, min(0.00022, tuning["panel_bevel_m"] * 0.40), 12.0)
        housing["v5_real_slot"] = True
        lens["v5_flush_or_recessed"] = True
        eye_records.append(side)
    faceplate["v5_topological_eye_slots"] = eye_records

    bezel = create_bezel_ring(
        faceplate_polygon,
        helmet_collection,
        projector,
        model,
        tuning,
        faceplate_offset,
    )
    assign_and_parent(bezel, "FaceplateBezel", materials, root, pivots["FaceplateBezel"], "Faceplate")
    if "FaceplateBezel" not in skip_micro_bevel_objects:
        add_micro_bevel(bezel, min(0.00045, tuning["panel_bevel_m"] * 0.70), 12.0)

    # --- Clean lower U/chin and exact bilateral craft symmetry --------------------------
    front_bbox = feature_data["views"]["front"]["helmet_silhouette_bbox_px"]
    # Replace the intersecting v4 lower shards with shared, clean polygon
    # boundaries.  These points remain inside the accepted front feature
    # layout but remove the crossed red fragments around the mouth/chin.
    bottom_raise_px = tuning.get("lower_plate_bottom_raise_px", 0.0)
    jaw_bottom_raise_px = tuning.get("jaw_plate_bottom_raise_px", bottom_raise_px)
    chin_bottom_raise_px = tuning.get("chin_plate_bottom_raise_px", bottom_raise_px)
    jaw_left_pixels = [
        (120, 413),
        (204, 410),
        (214, 444 - jaw_bottom_raise_px),
        (170, 442 - jaw_bottom_raise_px),
    ]
    jaw_left_uv = [v4.front_normalized(point, front_bbox) for point in jaw_left_pixels]
    raise_v = tuning["mouth_cover_raise_m"] / model.height
    jaw_left_uv[1] = (jaw_left_uv[1][0], jaw_left_uv[1][1] - raise_v)
    delete_object("Jaw_L")
    delete_object("Jaw_R")
    jaw_left = create_front_planar_prism(
        "Jaw_L",
        jaw_left_uv,
        helmet_collection,
        projector,
        0.0028,
        tuning.get(
            "jaw_plate_thickness_m",
            tuning.get("lower_plate_thickness_m", cfg.FIT["panel_thickness"]),
        ),
        tuning.get(
            "jaw_plate_seat_fraction",
            tuning.get("lower_plate_seat_fraction", 0.0),
        ),
    )
    assign_and_parent(jaw_left, "Jaw_L", materials, root, pivots["Jaw_L"])
    jaw_rear_shift = tuning.get("jaw_plate_rear_shift_m", 0.0)
    if jaw_rear_shift:
        transform_world_vertices(
            jaw_left,
            lambda point: Vector((point.x, point.y + jaw_rear_shift, point.z)),
        )
    jaw_width_scale = tuning.get("jaw_plate_width_scale_from_inner", 1.0)
    if jaw_width_scale != 1.0:
        inner_x = max(point.x for point in world_vertices(jaw_left))
        transform_world_vertices(
            jaw_left,
            lambda point: Vector(
                (inner_x + (point.x - inner_x) * jaw_width_scale, point.y, point.z)
            ),
        )
    if "Jaw_L" not in skip_micro_bevel_objects:
        add_micro_bevel(jaw_left, tuning["panel_bevel_m"], 14.0)
    jaw_right = mirror_left_to_right(
        "Jaw_L", "Jaw_R", helmet_collection, materials, root, pivots["Jaw_R"], None
    )
    jaw_right["v5_exact_mirror_of"] = "Jaw_L"

    chin_pixels = [
        (204, 410),
        (308, 410),
        (298, 444 - chin_bottom_raise_px),
        (214, 444 - chin_bottom_raise_px),
    ]
    chin_uv = [v4.front_normalized(point, front_bbox) for point in chin_pixels]
    for left_index, right_index in ((0, 1), (3, 2)):
        magnitude = 0.5 * (abs(chin_uv[left_index][0]) + abs(chin_uv[right_index][0]))
        v_average = 0.5 * (chin_uv[left_index][1] + chin_uv[right_index][1])
        chin_uv[left_index] = (-magnitude, v_average)
        chin_uv[right_index] = (magnitude, v_average)
    for index in (0, 1):
        chin_uv[index] = (chin_uv[index][0], chin_uv[index][1] - raise_v)
    delete_object("Chin")
    chin = create_front_planar_prism(
        "Chin",
        chin_uv,
        helmet_collection,
        projector,
        0.0028,
        tuning.get(
            "chin_plate_thickness_m",
            tuning.get("lower_plate_thickness_m", cfg.FIT["panel_thickness"]),
        ),
        tuning.get(
            "chin_plate_seat_fraction",
            tuning.get("lower_plate_seat_fraction", 0.0),
        ),
    )
    assign_and_parent(chin, "Chin", materials, root, pivots["Chin"])
    chin_rear_shift = tuning.get("chin_plate_rear_shift_m", 0.0)
    if chin_rear_shift:
        transform_world_vertices(
            chin,
            lambda point: Vector((point.x, point.y + chin_rear_shift, point.z)),
        )
    chin_width_scale = tuning.get("chin_plate_width_scale", 1.0)
    if chin_width_scale != 1.0:
        scale_about_world_bbox(chin, (chin_width_scale, 1.0, 1.0))
    if "Chin" not in skip_micro_bevel_objects:
        add_micro_bevel(chin, min(0.00030, tuning["panel_bevel_m"]), 14.0)

    mirror_pairs = (
        ("Cheek_L", "Cheek_R", None),
        ("JawTrim_L", "JawTrim_R", "Jaw_R"),
        ("SideGroove_L", "SideGroove_R", "Jaw_R"),
        ("RearLayer_L_Upper", "RearLayer_R_Upper", "RearShell_R"),
        ("RearLayer_L_Lower", "RearLayer_R_Lower", "RearShell_R"),
        ("RearVent_L_Upper", "RearVent_R_Upper", "RearShell_R"),
        ("RearVent_L_Lower", "RearVent_R_Lower", "RearShell_R"),
    )
    for left_name, right_name, owner in mirror_pairs:
        mirrored = mirror_left_to_right(
            left_name, right_name, helmet_collection, materials, root, pivots[right_name], owner
        )
        mirrored["v5_exact_mirror_of"] = left_name

    seat_faceplate_under_side_shells(faceplate)
    tighten_mouth_and_lower_frame(tuning)
    extend_crown_seam(tuning)
    inset_neck_ring(tuning)
    tuck_inner_shell_lower_rim(tuning)
    refine_spines(tuning, helmet_collection, materials, root, pivots, projector, model)

    # Apply one coherent craft bevel language to exposed plate edges.  Existing
    # v3 bevels are normalized in the v5 copy only.
    armor_names = (
        "RearShell_L", "RearShell_R", "Temple_L", "Temple_R",
        "Cheek_L", "Cheek_R", "Jaw_L", "Jaw_R",
        "FaceplateBezel", "EarCover_L", "EarCover_R",
        "JawTrim_L", "JawTrim_R", "LowerUTrim", "SideGroove_L", "SideGroove_R",
        "RearLayer_L_Upper", "RearLayer_R_Upper", "RearLayer_L_Lower", "RearLayer_R_Lower",
    )
    for name in armor_names:
        obj = bpy.data.objects[name]
        if name in skip_micro_bevel_objects:
            for modifier in list(obj.modifiers):
                if modifier.type == "BEVEL":
                    obj.modifiers.remove(modifier)
            continue
        existing_bevels = [modifier for modifier in obj.modifiers if modifier.type == "BEVEL"]
        if existing_bevels:
            for modifier in existing_bevels:
                modifier.width = tuning["panel_bevel_m"]
                modifier.segments = 2
                modifier.limit_method = "ANGLE"
                modifier.angle_limit = math.radians(14.0)
        else:
            add_micro_bevel(obj, tuning["panel_bevel_m"], 14.0)

    for obj in helmet_collection.all_objects:
        obj["aegis_version"] = 5
        obj["v5_iteration"] = args.iteration
    scene["aegis_v5_iteration"] = args.iteration
    scene["aegis_v5_tuning"] = json.dumps(tuning, sort_keys=True)
    scene["aegis_v5_source_blend"] = str(cfg.V4_BLEND_SOURCE)

    master_hash_after = mesh_hash(master)
    if master_hash_before != master_hash_after:
        raise RuntimeError("HelmetEnvelope_Master changed during v5 craft rebuild")
    final_names = {obj.name for obj in helmet_collection.all_objects}
    if final_names != expected_names or len(final_names) != cfg.EXPECTED_NODE_COUNT:
        raise RuntimeError(
            f"v5 node contract changed: missing={sorted(expected_names - final_names)} "
            f"extra={sorted(final_names - expected_names)} count={len(final_names)}"
        )
    for name in cfg.V4_NODE_NAMES:
        obj = bpy.data.objects[name]
        if (obj.matrix_world.translation - pivots[name]).length > 1.0e-7:
            raise RuntimeError(f"Pivot changed for {name}")
        parent_name = obj.parent.name if obj.parent else None
        if parent_name != parents[name]:
            raise RuntimeError(f"Parent changed for {name}: {parents[name]} -> {parent_name}")
        if any(value < 0.0 for value in obj.scale):
            raise RuntimeError(f"Negative scale on {name}")

    triangles = evaluated_triangle_count(list(helmet_collection.all_objects))
    if triangles >= cfg.TRIANGLE_LIMIT:
        raise RuntimeError(f"v5 evaluated triangle count exceeds target: {triangles}")
    if projector.misses:
        raise RuntimeError(f"v5 projection misses: {projector.misses[:3]}")

    # Preserve the proven v4 named clearance gate.  InnerShell and HeadProxy
    # are immutable v4 inputs, while the additional realized faceplate sample
    # records the rebuilt v5 nose surface independently of the old analytic
    # faceplate formula.
    clearance = base.clearance_metrics(model)
    nose_tip_y = cfg.HEAD["nose_center"][1] - cfg.HEAD["nose_radii"][1]
    nose_surface, _ = projector.front(0.0, 0.58, faceplate_offset(0.0, 0.58))
    clearance["v5_realized_faceplate_nose_surface_y_m"] = round(float(nose_surface.y), 6)
    clearance["v5_realized_faceplate_nose_clearance_m"] = round(
        float(nose_tip_y - nose_surface.y), 6
    )
    clearance["canonical_gate"] = {
        "method": "protected v4 HeadProxy named samples against immutable InnerShell",
        "minimum_required_m": 0.004,
        "pass": (
            clearance["minimum_sample_m"] >= 0.004
            and not clearance["penetration_detected"]
        ),
    }
    if not clearance["canonical_gate"]["pass"]:
        raise RuntimeError(f"v5 canonical HeadProxy clearance failed: {clearance}")

    cfg.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    cfg.WORK_DIR.mkdir(parents=True, exist_ok=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=str(cfg.BLEND_PATH))
    build_log = {
        "script": str(Path(__file__).resolve()),
        "iteration": args.iteration,
        "source_v4_blend": str(cfg.V4_BLEND_SOURCE),
        "v5_blend": str(cfg.BLEND_PATH),
        "tuning": tuning,
        "master_contract": {
            "hash_before": master_hash_before,
            "hash_after": master_hash_after,
            "unchanged": master_hash_before == master_hash_after,
        },
        "node_contract": {
            "expected_count": cfg.EXPECTED_NODE_COUNT,
            "actual_count": len(final_names),
            "names": sorted(final_names),
            "parents_and_pivots_preserved": True,
        },
        "faceplate": {
            "surface_method": faceplate.get("v5_surface_method"),
            "topological_slot_health": faceplate_health,
            "slot_method": "explicit constrained topology with closed thickness walls",
            "eye_slots": eye_records,
        },
        "projection": {
            "surface_hits": projector.hits,
            "ray_misses": projector.misses,
            "edge_clamps": projector.clamps,
            "hit_ratio": 1.0,
        },
        "clearance": clearance,
        "evaluated_triangle_count": triangles,
        "triangle_limit_exclusive": cfg.TRIANGLE_LIMIT,
        "elapsed_seconds": round(time.perf_counter() - started, 4),
        "known_issues": [
            "Craft-pass graybox only; small fasteners and production retopology remain out of scope.",
            "No PBR, UV, rig, animation, or Web integration was performed.",
        ],
    }
    (cfg.LOG_DIR / f"iteration_{args.iteration}_build.json").write_text(
        json.dumps(build_log, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(
        f"AEGIS_V5_BUILD_OK iteration={args.iteration} nodes={len(final_names)} "
        f"triangles={triangles} projection_hits={projector.hits}"
    )
    print(f"AEGIS_V5_CRAFT={tuning['label']}")
    print(f"AEGIS_V5_BLEND={cfg.BLEND_PATH}")


if __name__ == "__main__":
    main()
