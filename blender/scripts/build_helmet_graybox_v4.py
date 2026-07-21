"""Build AEGIS-R7 graybox v4 by projecting measured crop features.

The protected v3 blend is opened as the immutable envelope/pivot baseline.
Only the failed visible feature pieces are replaced; the measured master loft,
inner shell, head proxy, cameras, neck ring, and stable transforms are reused.
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

import bpy
import bmesh
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import delaunay_2d_cdt


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import helmet_v4_config as cfg
import build_helmet_graybox_v3 as base


base.cfg = cfg
V3_BLEND_SOURCE = cfg.WORK_DIR / "helmet_graybox_v3.blend"
FRONT_AXIS_X = 268.0


def parse_args() -> argparse.Namespace:
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--iteration", type=int, default=1)
    return parser.parse_args(arguments)


def mesh_hash(obj: bpy.types.Object) -> str:
    digest = hashlib.sha256()
    digest.update(obj.name.encode("utf-8"))
    for vertex in obj.data.vertices:
        digest.update((f"{vertex.co.x:.9f},{vertex.co.y:.9f},{vertex.co.z:.9f};").encode("ascii"))
    for polygon in obj.data.polygons:
        digest.update((",".join(str(index) for index in polygon.vertices) + ";").encode("ascii"))
    for modifier in obj.modifiers:
        digest.update(f"{modifier.name}:{modifier.type};".encode("utf-8"))
    return digest.hexdigest()


def feature_points(data: dict, view: str, name: str) -> list[tuple[float, float]]:
    feature = data["views"][view]["features"][name]
    return [tuple(record["source_px"]) for record in feature["points"]]


def point_in_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    x, y = point
    inside = False
    previous = polygon[-1]
    for current in polygon:
        x0, y0 = previous
        x1, y1 = current
        if (y0 > y) != (y1 > y):
            intersect = (x1 - x0) * (y - y0) / (y1 - y0) + x0
            if x < intersect:
                inside = not inside
        previous = current
    return inside


def polygon_area_xz(points: list[tuple[float, float]]) -> float:
    return 0.5 * sum(
        x0 * z1 - x1 * z0
        for (x0, z0), (x1, z1) in zip(points, points[1:] + points[:1])
    )


def constrained_triangulation(
    polygon_uv: list[tuple[float, float]], spacing: float = 0.030
) -> tuple[list[tuple[float, float]], list[tuple[int, ...]], list[tuple[int, int]]]:
    # Delaunay works in an x/up plane, hence z=-v for top-origin source pixels.
    boundary = [(u, -v) for u, v in polygon_uv]
    if polygon_area_xz(boundary) < 0.0:
        boundary.reverse()
        polygon_uv = list(reversed(polygon_uv))
    coordinates = [Vector(point) for point in boundary]
    min_u = min(point[0] for point in polygon_uv)
    max_u = max(point[0] for point in polygon_uv)
    min_v = min(point[1] for point in polygon_uv)
    max_v = max(point[1] for point in polygon_uv)
    row = min_v + spacing
    while row < max_v - spacing * 0.5:
        column = min_u + spacing
        while column < max_u - spacing * 0.5:
            if point_in_polygon((column, row), polygon_uv):
                coordinates.append(Vector((column, -row)))
            column += spacing
        row += spacing
    boundary_count = len(boundary)
    edges = [(index, (index + 1) % boundary_count) for index in range(boundary_count)]
    result = delaunay_2d_cdt(
        coordinates,
        edges,
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
    boundary_edges = [edge for edge, count in edge_counts.items() if count == 1]
    return uv, faces, boundary_edges


class MasterSurfaceProjector:
    def __init__(self, master: bpy.types.Object, model: base.EnvelopeModel):
        self.master = master
        self.model = model
        self.depsgraph = bpy.context.evaluated_depsgraph_get()
        self.bvh = BVHTree.FromObject(master, self.depsgraph)
        evaluated = master.evaluated_get(self.depsgraph)
        mesh = evaluated.to_mesh()
        coordinates = [master.matrix_world @ vertex.co for vertex in mesh.vertices]
        evaluated.to_mesh_clear()
        self.minimum = Vector(tuple(min(point[axis] for point in coordinates) for axis in range(3)))
        self.maximum = Vector(tuple(max(point[axis] for point in coordinates) for axis in range(3)))
        self.height = model.height
        self.hits = 0
        self.misses: list[dict] = []
        self.clamps: list[dict] = []

    def _cast(self, origin: Vector, direction: Vector, expected_normal: Vector, label: str) -> tuple[Vector, Vector]:
        location, normal, _, _ = self.bvh.ray_cast(origin, direction, 0.5)
        if location is None:
            self.misses.append({"label": label, "origin": list(origin), "direction": list(direction)})
            raise RuntimeError(f"Projection miss for {label}: {tuple(origin)}")
        self.hits += 1
        normal = normal.normalized()
        if normal.dot(expected_normal) < 0.0:
            normal.negate()
        return location, normal

    def front(self, u: float, v: float, offset: float = 0.0) -> tuple[Vector, Vector]:
        x = u * self.height
        section = self.model.section(max(0.0, min(1.0, v)))
        limit = section["half_width"] * 0.985
        if abs(x) > limit:
            self.clamps.append({"view": "front", "requested_x": x, "clamped_x": math.copysign(limit, x), "v": v})
            x = math.copysign(limit, x)
        z = self.model.top_z - v * self.height
        point, normal = self._cast(
            Vector((x, self.minimum.y - 0.040, z)), Vector((0.0, 1.0, 0.0)), Vector((0.0, -1.0, 0.0)), "front"
        )
        return point + normal * offset, normal

    def back(self, u: float, v: float, offset: float = 0.0) -> tuple[Vector, Vector]:
        x = u * self.height
        section = self.model.section(max(0.0, min(1.0, v)))
        limit = section["half_width"] * 0.985
        if abs(x) > limit:
            self.clamps.append({"view": "back", "requested_x": x, "clamped_x": math.copysign(limit, x), "v": v})
            x = math.copysign(limit, x)
        z = self.model.top_z - v * self.height
        point, normal = self._cast(
            Vector((x, self.maximum.y + 0.040, z)), Vector((0.0, -1.0, 0.0)), Vector((0.0, 1.0, 0.0)), "back"
        )
        return point + normal * offset, normal

    def side(self, view: str, screen_u: float, v: float, offset: float = 0.0) -> tuple[Vector, Vector]:
        requested_v = v
        v = max(0.012, min(0.985, v))
        if abs(v - requested_v) > 1.0e-9:
            self.clamps.append({"view": view, "requested_v": requested_v, "clamped_v": v})
        y = screen_u * self.height if view == "right" else -screen_u * self.height
        section = self.model.section(max(0.0, min(1.0, v)))
        y_limit_min = section["front_y"] * 0.985
        y_limit_max = section["rear_y"] * 0.985
        clamped_y = max(y_limit_min, min(y_limit_max, y))
        if abs(clamped_y - y) > 1.0e-9:
            self.clamps.append({"view": view, "requested_y": y, "clamped_y": clamped_y, "v": v})
            y = clamped_y
        z = self.model.top_z - v * self.height
        if view == "right":
            origin = Vector((self.maximum.x + 0.040, y, z))
            direction = Vector((-1.0, 0.0, 0.0))
            expected = Vector((1.0, 0.0, 0.0))
        else:
            origin = Vector((self.minimum.x - 0.040, y, z))
            direction = Vector((1.0, 0.0, 0.0))
            expected = Vector((-1.0, 0.0, 0.0))
        point, normal = self._cast(origin, direction, expected, view)
        return point + normal * offset, normal

    def top(self, u: float, v: float, offset: float = 0.0) -> tuple[Vector, Vector]:
        x = u * self.height
        y = v * self.height
        point, normal = self._cast(
            Vector((x, y, self.maximum.z + 0.040)), Vector((0.0, 0.0, -1.0)), Vector((0.0, 0.0, 1.0)), "top"
        )
        return point + normal * offset, normal


def front_normalized(point: tuple[float, float], bbox: list[int]) -> tuple[float, float]:
    height = bbox[3] - bbox[1]
    return ((point[0] - FRONT_AXIS_X) / height, (point[1] - bbox[1]) / height)


def side_normalized(point: tuple[float, float], bbox: list[int]) -> tuple[float, float]:
    height = bbox[3] - bbox[1]
    center = 0.5 * (bbox[0] + bbox[2])
    return ((point[0] - center) / height, (point[1] - bbox[1]) / height)


def create_projected_closed_mesh(
    name: str,
    polygon_uv: list[tuple[float, float]],
    collection: bpy.types.Collection,
    project: Callable[[float, float, float], tuple[Vector, Vector]],
    offset: float,
    thickness: float,
    spacing: float = 0.035,
    extra_offset: Callable[[float, float], float] | None = None,
) -> bpy.types.Object:
    uv, surface_faces, boundary_edges = constrained_triangulation(polygon_uv, spacing)
    outer: list[Vector] = []
    inner: list[Vector] = []
    for u, v in uv:
        local_offset = offset + (extra_offset(u, v) if extra_offset else 0.0)
        point, normal = project(u, v, local_offset)
        outer.append(point)
        inner.append(point - normal * thickness)
    count = len(outer)
    vertices = outer + inner
    faces = list(surface_faces)
    faces.extend(tuple(reversed(tuple(index + count for index in face))) for face in surface_faces)
    for first, second in boundary_edges:
        faces.append((first, second, second + count, first + count))
    obj = base.create_mesh_object(name, vertices, faces, collection)
    # Keep the projected skin smooth while splitting its explicit thickness
    # walls.  Averaging side-wall normals into the skin created a false sawtooth
    # highlight along the measured faceplate boundary.
    surface_count = len(surface_faces)
    for index, polygon in enumerate(obj.data.polygons):
        polygon.use_smooth = index < surface_count * 2
    return obj


def create_projected_ring(
    name: str,
    inner_uv: list[tuple[float, float]],
    outer_uv: list[tuple[float, float]],
    collection: bpy.types.Collection,
    project: Callable[[float, float, float], tuple[Vector, Vector]],
    offset: float,
) -> bpy.types.Object:
    vertices: list[Vector] = []
    for polygon in (inner_uv, outer_uv):
        for u, v in polygon:
            point, _ = project(u, v, offset)
            vertices.append(point)
    count = len(inner_uv)
    faces = []
    for index in range(count):
        following = (index + 1) % count
        faces.append((index, following, count + following, count + index))
    obj = base.create_mesh_object(name, vertices, faces, collection)
    base.add_editable_modifiers(obj, 0.0014, 0.00035, 0)
    return obj


def polyline_ribbon(points: list[tuple[float, float]], half_width: float) -> list[tuple[float, float]]:
    left = []
    right = []
    for index, point in enumerate(points):
        previous = Vector(points[max(0, index - 1)])
        following = Vector(points[min(len(points) - 1, index + 1)])
        tangent = (following - previous).normalized()
        normal = Vector((-tangent.y, tangent.x))
        current = Vector(point)
        left.append(tuple(current + normal * half_width))
        right.append(tuple(current - normal * half_width))
    return left + list(reversed(right))


def rounded_slot_uv(
    center: tuple[float, float], width: float, height: float, angle_degrees: float
) -> list[tuple[float, float]]:
    half_width = width * 0.5
    half_height = height * 0.5
    bevel = min(half_width * 0.15, half_height * 0.62)
    local = [
        (-half_width + bevel, -half_height),
        (half_width - bevel, -half_height),
        (half_width, -half_height + bevel),
        (half_width, half_height - bevel),
        (half_width - bevel, half_height),
        (-half_width + bevel, half_height),
        (-half_width, half_height - bevel),
        (-half_width, -half_height + bevel),
    ]
    angle = math.radians(angle_degrees)
    cosine = math.cos(angle)
    sine = math.sin(angle)
    return [
        (
            center[0] + x * cosine - y * sine,
            center[1] + x * sine + y * cosine,
        )
        for x, y in local
    ]


def faceplate_relief(u: float, v: float, strength: float) -> float:
    edge = min(1.0, abs(u) / 0.39)
    edge_fade = max(0.0, 1.0 - edge * edge) ** 1.6
    center = math.exp(-((u / 0.115) ** 2))
    forehead = 0.0030 * math.exp(-(((v - 0.27) / 0.10) ** 2))
    brow = 0.0080 * math.exp(-(((v - 0.385) / 0.038) ** 2)) * (0.48 + 0.52 * center)
    nose = 0.0150 * math.exp(-(((v - 0.51) / 0.14) ** 2)) * center
    mouth_recess = -0.0042 * math.exp(-(((v - 0.665) / 0.050) ** 2)) * center
    chin = 0.0110 * math.exp(-(((v - 0.775) / 0.060) ** 2)) * math.exp(-((u / 0.14) ** 2))
    top_fade = max(0.0, min(1.0, (v - 0.205) / 0.080))
    bottom_fade = max(0.0, min(1.0, (0.825 - v) / 0.070))
    vertical_fade = top_fade * top_fade * (3.0 - 2.0 * top_fade) * bottom_fade * bottom_fade * (3.0 - 2.0 * bottom_fade)
    return strength * edge_fade * vertical_fade * (forehead + brow + nose + mouth_recess + chin)


def create_box(name: str, center: Vector, dimensions: tuple[float, float, float], collection: bpy.types.Collection) -> bpy.types.Object:
    dx, dy, dz = (value * 0.5 for value in dimensions)
    vertices = [
        center + Vector((sx * dx, sy * dy, sz * dz))
        for sx, sy, sz in ((-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1), (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1))
    ]
    faces = [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (4, 0, 3, 7)]
    return base.create_mesh_object(name, vertices, faces, collection)


def create_ear_cover_v4(name: str, side_sign: float, center: Vector, radius: float, collection: bpy.types.Collection) -> bpy.types.Object:
    offsets = (-0.006, -0.003, 0.000, 0.004, 0.008, 0.011)
    radii = (radius * 0.84, radius, radius * 1.04, radius * 0.98, radius * 0.86, radius * 0.70)
    segments = 56
    vertices = []
    for axial, ring_radius in zip(offsets, radii):
        x = center.x + side_sign * axial
        for index in range(segments):
            angle = 2.0 * math.pi * index / segments
            vertices.append(Vector((x, center.y + ring_radius * math.cos(angle), center.z + ring_radius * math.sin(angle))))
    faces = []
    for ring in range(len(offsets) - 1):
        current = ring * segments
        following_ring = (ring + 1) * segments
        for index in range(segments):
            following = (index + 1) % segments
            faces.append((current + index, current + following, following_ring + following, following_ring + index))
    faces.extend((tuple(reversed(range(segments))), tuple(range((len(offsets) - 1) * segments, len(offsets) * segments))))
    if side_sign < 0.0:
        faces = [tuple(reversed(face)) for face in faces]
    return base.create_mesh_object(name, vertices, faces, collection)


def create_torus_x(name: str, side_sign: float, center: Vector, major: float, minor: float, axial: float, collection: bpy.types.Collection) -> bpy.types.Object:
    major_steps = 48
    minor_steps = 8
    vertices = []
    for major_index in range(major_steps):
        angle = 2.0 * math.pi * major_index / major_steps
        radial_y = math.cos(angle)
        radial_z = math.sin(angle)
        for minor_index in range(minor_steps):
            cross = 2.0 * math.pi * minor_index / minor_steps
            radius = major + minor * math.cos(cross)
            vertices.append(Vector((center.x + side_sign * (axial + minor * math.sin(cross)), center.y + radius * radial_y, center.z + radius * radial_z)))
    faces = []
    for major_index in range(major_steps):
        next_major = (major_index + 1) % major_steps
        for minor_index in range(minor_steps):
            next_minor = (minor_index + 1) % minor_steps
            faces.append((major_index * minor_steps + minor_index, next_major * minor_steps + minor_index, next_major * minor_steps + next_minor, major_index * minor_steps + next_minor))
    return base.create_mesh_object(name, vertices, faces, collection)


def create_disc_x(name: str, side_sign: float, center: Vector, radius: float, axial: float, collection: bpy.types.Collection) -> bpy.types.Object:
    segments = 48
    depth = 0.0025
    vertices = []
    for offset in (axial - depth * 0.5, axial + depth * 0.5):
        x = center.x + side_sign * offset
        for index in range(segments):
            angle = 2.0 * math.pi * index / segments
            vertices.append(Vector((x, center.y + radius * math.cos(angle), center.z + radius * math.sin(angle))))
    faces = []
    for index in range(segments):
        following = (index + 1) % segments
        faces.append((index, following, segments + following, segments + index))
    faces.extend((tuple(reversed(range(segments))), tuple(range(segments, 2 * segments))))
    if side_sign < 0.0:
        faces = [tuple(reversed(face)) for face in faces]
    return base.create_mesh_object(name, vertices, faces, collection)


def assign_and_parent(
    obj: bpy.types.Object,
    material_key: str,
    materials: dict[str, bpy.types.Material],
    root: bpy.types.Object,
    pivot: Vector,
    owner: str | None = None,
) -> None:
    base.assign_part_materials(obj, material_key, materials)
    obj["aegis_v4_feature"] = True
    if owner:
        obj["feature_owner"] = owner
    base.set_origin_preserve_world_geometry(obj, pivot)
    base.parent_to_root(obj, root)


def delete_objects(names: list[str]) -> None:
    for name in names:
        obj = bpy.data.objects.get(name)
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    if args.iteration < 1:
        raise ValueError("iteration must be >= 1")
    if not V3_BLEND_SOURCE.is_file() or not cfg.FEATURE_CURVES_PATH.is_file():
        raise FileNotFoundError("v3 blend and feature_curves.json are required")
    cfg.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    cfg.WORK_DIR.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.open_mainfile(filepath=str(V3_BLEND_SOURCE))
    scene = bpy.context.scene
    scene.name = "AEGIS-R7 Graybox v4"
    master = bpy.data.objects.get("HelmetEnvelope_Master")
    root = bpy.data.objects.get("HelmetRoot")
    helmet_collection = bpy.data.collections.get("HELMET_V3")
    if master is None or root is None or helmet_collection is None:
        raise RuntimeError("Protected v3 envelope contract is incomplete")
    master_hash_before = mesh_hash(master)
    feature_data = json.loads(cfg.FEATURE_CURVES_PATH.read_text(encoding="utf-8"))
    measurements = json.loads(cfg.MEASUREMENTS_PATH.read_text(encoding="utf-8"))
    model = base.EnvelopeModel(measurements)
    projector = MasterSurfaceProjector(master, model)

    replaced = [
        "Faceplate", "EyeHousing_L", "EyeHousing_R", "EyeLens_L", "EyeLens_R",
        "Cheek_L", "Cheek_R", "Jaw_L", "Jaw_R", "Chin",
        "EarCover_L", "EarCover_R", "EarEmitter_L", "EarEmitter_R",
    ]
    pivots = {name: bpy.data.objects[name].matrix_world.translation.copy() for name in replaced if name in bpy.data.objects}
    owner_pivots = {
        name: bpy.data.objects[name].matrix_world.translation.copy()
        for name in ("CrownFront", "CrownRear", "RearShell_L", "RearShell_R")
    }
    delete_objects(replaced)
    # The v3 temple classifiers intruded into the measured front faceplate.
    # Retain the stable nodes/pivots but seat their vertices 6 mm rearward so
    # the measured gold boundary, not the old analytic boundary, is visible.
    for temple_name in ("Temple_L", "Temple_R"):
        temple = bpy.data.objects[temple_name]
        for vertex in temple.data.vertices:
            vertex.co.y += 0.0060
    crown_front = bpy.data.objects["CrownFront"]
    for vertex in crown_front.data.vertices:
        vertex.co.y += 0.0040

    materials = {}
    for name, specification in cfg.MATERIALS.items():
        materials[name] = bpy.data.materials.get(name) or base.make_material(name, specification)

    tuning = {
        1: {"relief": 0.76, "ear": 0.90, "trim": 0.78, "label": "measured projection first pass"},
        2: {"relief": 1.12, "ear": 0.97, "trim": 0.96, "label": "eye topology, depth, ridge and groove correction"},
    }.get(args.iteration, {"relief": 1.08, "ear": 1.0, "trim": 1.0, "label": "final eye visibility, boundary continuity and crown-cap convergence"})

    front_bbox = feature_data["views"]["front"]["helmet_silhouette_bbox_px"]
    faceplate_pixels = feature_points(feature_data, "front", "faceplate_outer_boundary")
    faceplate_uv = [front_normalized(point, front_bbox) for point in faceplate_pixels]
    faceplate = create_projected_closed_mesh(
        "Faceplate", faceplate_uv, helmet_collection, projector.front, 0.0045, cfg.FIT["faceplate_thickness"],
        spacing=0.024, extra_offset=lambda u, v: faceplate_relief(u, v, tuning["relief"]),
    )
    assign_and_parent(faceplate, "Faceplate", materials, root, pivots["Faceplate"])

    centroid = Vector((sum(point[0] for point in faceplate_uv) / len(faceplate_uv), sum(point[1] for point in faceplate_uv) / len(faceplate_uv)))
    bezel_width = (cfg.FIT["bezel_width"] / model.height) * tuning["trim"]
    bezel_outer = []
    for point in faceplate_uv:
        direction = Vector(point) - centroid
        direction.normalize()
        bezel_outer.append(tuple(Vector(point) + direction * bezel_width))
    bezel = create_projected_ring("FaceplateBezel", faceplate_uv, bezel_outer, helmet_collection, projector.front, 0.0032)
    assign_and_parent(bezel, "FaceplateBezel", materials, root, pivots["Faceplate"], "Faceplate")

    def face_project(u: float, v: float, offset: float) -> tuple[Vector, Vector]:
        return projector.front(u, v, 0.0045 + faceplate_relief(u, v, tuning["relief"]) + offset)

    for side in ("L", "R"):
        key = "left" if side == "L" else "right"
        metric = feature_data["views"]["front"]["features"][f"eye_{key}_metrics"]
        center = tuple(metric["center_normalized_head_height"])
        angle = 3.2 if side == "L" else -3.2
        outer = rounded_slot_uv(center, metric["length_head_height"] * 1.23, 0.043, angle)
        lens_uv = rounded_slot_uv(center, metric["length_head_height"], 0.0175, angle)
        housing = create_projected_closed_mesh(f"EyeHousing_{side}", outer, helmet_collection, face_project, 0.0045, 0.0020, spacing=0.060)
        lens = create_projected_closed_mesh(f"EyeLens_{side}", lens_uv, helmet_collection, face_project, 0.0060, 0.0012, spacing=0.060)
        assign_and_parent(housing, f"EyeHousing_{side}", materials, root, pivots[f"EyeHousing_{side}"])
        assign_and_parent(lens, f"EyeLens_{side}", materials, root, pivots[f"EyeLens_{side}"])

    panel_polygons = {
        "Cheek_L": [(99, 241), (130, 249), (170, 267), (198, 326), (218, 386), (201, 405), (169, 442), (120, 413), (98, 354), (94, 282)],
        "Cheek_R": [(537 - x, y) for x, y in [(99, 241), (130, 249), (170, 267), (198, 326), (218, 386), (201, 405), (169, 442), (120, 413), (98, 354), (94, 282)]],
        "Jaw_L": [(120, 413), (169, 442), (201, 405), (230, 414), (221, 468), (183, 466)],
        "Jaw_R": [(537 - x, y) for x, y in [(120, 413), (169, 442), (201, 405), (230, 414), (221, 468), (183, 466)]],
        "Chin": [(201, 405), (230, 414), (282, 414), (304, 405), (335, 462), (292, 478), (221, 478), (181, 462)],
    }
    for name, pixels in panel_polygons.items():
        uv = [front_normalized(point, front_bbox) for point in pixels]
        part = create_projected_closed_mesh(name, uv, helmet_collection, projector.front, 0.0028, cfg.FIT["panel_thickness"], spacing=0.040)
        assign_and_parent(part, name, materials, root, pivots[name])

    for side, pixels in (
        ("L", feature_points(feature_data, "front", "jaw_u_left")),
        ("R", feature_points(feature_data, "front", "jaw_u_right")),
    ):
        uv_line = [front_normalized(point, front_bbox) for point in pixels]
        ribbon = polyline_ribbon(uv_line, 0.0065 * tuning["trim"])
        trim = create_projected_closed_mesh(f"JawTrim_{side}", ribbon, helmet_collection, projector.front, 0.0040, 0.0014, spacing=0.050)
        assign_and_parent(trim, f"JawTrim_{side}", materials, root, pivots[f"Jaw_{side}"], f"Jaw_{side}")
    lower_line = [front_normalized(point, front_bbox) for point in feature_points(feature_data, "front", "lower_u")]
    lower_trim = create_projected_closed_mesh("LowerUTrim", polyline_ribbon(lower_line, 0.0060), helmet_collection, projector.front, 0.0040, 0.0014, spacing=0.050)
    assign_and_parent(lower_trim, "LowerUTrim", materials, root, pivots["Chin"], "Chin")

    side_grooves = {
        "SideGroove_L": ("left", feature_points(feature_data, "left", "cheek_to_jaw_diagonal")),
        "SideGroove_R": ("right", feature_points(feature_data, "right", "cheek_to_jaw_diagonal")),
    }
    for name, (view, pixels) in side_grooves.items():
        bbox = feature_data["views"][view]["helmet_silhouette_bbox_px"]
        line = [side_normalized(point, bbox) for point in pixels]
        uv = polyline_ribbon(line, 0.0060)
        project = lambda u, v, offset, selected=view: projector.side(selected, u, v, offset)
        part = create_projected_closed_mesh(name, uv, helmet_collection, project, 0.0055, 0.0016, spacing=0.050)
        owner = "Jaw_L" if name.endswith("_L") else "Jaw_R"
        assign_and_parent(part, name, materials, root, pivots[owner], owner)

    ear_radius = cfg.FIT["ear_radius"] * tuning["ear"]
    for side, sign in (("L", -1.0), ("R", 1.0)):
        center = model.point(0.505, math.pi if sign < 0.0 else 0.0)
        ear = create_ear_cover_v4(f"EarCover_{side}", sign, center, ear_radius, helmet_collection)
        outer_ring = create_torus_x(f"EarRing_{side}_Outer", sign, center, ear_radius * 0.78, 0.0032, 0.0120, helmet_collection)
        inner_disc = create_disc_x(f"EarRing_{side}_Inner", sign, center, ear_radius * 0.58, 0.0135, helmet_collection)
        emitter_center = center + Vector((sign * 0.0160, -0.0010, 0.0))
        emitter = create_box(f"EarEmitter_{side}", emitter_center, (0.0036, 0.0070, model.height * 0.170), helmet_collection)
        assign_and_parent(ear, f"EarCover_{side}", materials, root, pivots[f"EarCover_{side}"])
        assign_and_parent(outer_ring, f"EarRing_{side}_Outer", materials, root, pivots[f"EarCover_{side}"], f"EarCover_{side}")
        assign_and_parent(inner_disc, f"EarRing_{side}_Inner", materials, root, pivots[f"EarCover_{side}"], f"EarCover_{side}")
        assign_and_parent(emitter, f"EarEmitter_{side}", materials, root, pivots[f"EarEmitter_{side}"], f"EarCover_{side}")

    front_spine_ranges = [(0.012, 0.052), (0.061, 0.102), (0.111, 0.153), (0.162, 0.205)]
    for index, (start, end) in enumerate(front_spine_ranges, 1):
        half_width = (cfg.FIT["spine_width"] * 0.42) / model.height
        uv = [(-half_width, start), (half_width, start), (half_width, end), (-half_width, end)]
        part = create_projected_closed_mesh(f"CrownSpine_{index:02d}", uv, helmet_collection, projector.front, 0.0090, 0.0018, spacing=0.050)
        assign_and_parent(part, f"CrownSpine_{index:02d}", materials, root, owner_pivots["CrownFront"], "CrownFront")
    rear_spine_ranges = [(0.015, 0.105), (0.115, 0.205), (0.215, 0.305), (0.315, 0.405), (0.415, 0.505), (0.515, 0.605), (0.615, 0.705), (0.715, 0.865)]
    for index, (start, end) in enumerate(rear_spine_ranges, 1):
        width_factor = 0.48 if start < 0.20 else 0.55
        half_width = (cfg.FIT["spine_width"] * width_factor) / model.height
        uv = [(-half_width, start), (half_width, start), (half_width, end), (-half_width, end)]
        part = create_projected_closed_mesh(f"RearSpine_{index:02d}", uv, helmet_collection, projector.back, 0.0100, 0.0018, spacing=0.050)
        assign_and_parent(part, f"RearSpine_{index:02d}", materials, root, owner_pivots["CrownRear"], "CrownRear")
    cap = create_box("CrownSpine_Cap", Vector((0.0, 0.0, model.top_z + 0.0048)), (cfg.FIT["spine_width"] * 1.15, 0.072, 0.0060), helmet_collection)
    base.add_editable_modifiers(cap, 0.0, 0.0015, 0)
    assign_and_parent(cap, "CrownSpine_Cap", materials, root, owner_pivots["CrownFront"], "CrownFront")

    for side, sign in (("L", -1.0), ("R", 1.0)):
        for level, v in (("Upper", 0.474), ("Lower", 0.565)):
            center_u = sign * 0.188
            half_width = 0.045
            half_height = 0.010
            uv = [(center_u - half_width, v - half_height), (center_u + half_width, v - half_height), (center_u + half_width, v + half_height), (center_u - half_width, v + half_height)]
            name = f"RearVent_{side}_{level}"
            part = create_projected_closed_mesh(name, uv, helmet_collection, projector.back, 0.0110, 0.0015, spacing=0.060)
            owner = f"RearShell_{side}"
            assign_and_parent(part, name, materials, root, owner_pivots[owner], owner)
        for level, points in (
            ("Upper", [(-0.10 * sign, 0.23), (-0.20 * sign, 0.25), (-0.29 * sign, 0.29), (-0.34 * sign, 0.34)]),
            ("Lower", [(-0.09 * sign, 0.65), (-0.18 * sign, 0.67), (-0.26 * sign, 0.72), (-0.31 * sign, 0.79)]),
        ):
            # Re-express sides explicitly so L occupies negative world x.
            line = [(sign * abs(u), v) for u, v in points]
            ribbon = polyline_ribbon(line, 0.0060)
            name = f"RearLayer_{side}_{level}"
            part = create_projected_closed_mesh(name, ribbon, helmet_collection, projector.back, 0.0065, 0.0014, spacing=0.050)
            owner = f"RearShell_{side}"
            assign_and_parent(part, name, materials, root, owner_pivots[owner], owner)

    for obj in bpy.data.objects:
        if obj.get("aegis_v3_managed") or obj.get("aegis_v4_feature"):
            obj["aegis_version"] = 4
    # All v4 procedural solids are closed.  Normalize their winding once at
    # the data level so signed-volume and glTF front-face audits agree.
    for obj in helmet_collection.all_objects:
        if obj.type != "MESH" or not obj.get("aegis_v4_feature"):
            continue
        mesh_bm = bmesh.new()
        try:
            mesh_bm.from_mesh(obj.data)
            bmesh.ops.recalc_face_normals(mesh_bm, faces=list(mesh_bm.faces))
            mesh_bm.to_mesh(obj.data)
            obj.data.update()
        finally:
            mesh_bm.free()
    master_hash_after = mesh_hash(master)
    if master_hash_before != master_hash_after:
        raise RuntimeError("HelmetEnvelope_Master changed during v4 feature rebuild")
    missing = [name for name in cfg.REQUIRED_EXPORT_PARTS if bpy.data.objects.get(name) is None]
    if missing:
        raise RuntimeError(f"Stable v3 node contract missing after v4 rebuild: {missing}")
    if projector.misses:
        raise RuntimeError(f"Feature projection contained ray misses: {projector.misses[:3]}")
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=str(cfg.BLEND_PATH))

    manifest = {
        "script": str(Path(__file__).resolve()),
        "iteration": args.iteration,
        "source_v3_blend": str(V3_BLEND_SOURCE),
        "v4_blend": str(cfg.BLEND_PATH),
        "tuning": tuning,
        "master_contract": {"hash_before": master_hash_before, "hash_after": master_hash_after, "unchanged": True},
        "projection": {"surface_hits": projector.hits, "ray_misses": projector.misses, "edge_clamps": projector.clamps, "hit_ratio": 1.0},
        "stable_nodes_present": list(cfg.REQUIRED_EXPORT_PARTS),
        "mesh_object_count": len([obj for obj in helmet_collection.all_objects if obj.type == "MESH"]),
        "clearance": base.clearance_metrics(model),
        "elapsed_seconds": round(time.perf_counter() - started, 4),
    }
    (cfg.LOG_DIR / f"iteration_{args.iteration}_build.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"AEGIS_V4_BUILD_OK iteration={args.iteration} meshes={manifest['mesh_object_count']} hits={projector.hits}")
    print(f"AEGIS_V4_BLEND={cfg.BLEND_PATH}")


if __name__ == "__main__":
    main()
