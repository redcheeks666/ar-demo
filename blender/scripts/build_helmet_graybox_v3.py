"""Build the reference-driven AEGIS-R7 helmet graybox v3.

Core geometry is a multi-section asymmetric loft sampled from front and side
silhouettes. Visible parts are extracted from that shared cage; the legacy
ellipsoid-patch and fixed-depth-prism algorithms are intentionally not used.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Callable

import bpy
from mathutils import Matrix, Vector


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import helmet_v3_config as cfg


def parse_args() -> argparse.Namespace:
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--iteration", type=int, default=1)
    parser.add_argument("--envelope-only", action="store_true")
    return parser.parse_args(arguments)


def ensure_directories() -> None:
    for path in (cfg.DERIVED_REFERENCE_DIR, cfg.OUTPUT_DIR, cfg.LOG_DIR, cfg.WORK_DIR):
        path.mkdir(parents=True, exist_ok=True)


def remove_managed_collections() -> None:
    for name in cfg.MANAGED_COLLECTIONS:
        collection = bpy.data.collections.get(name)
        if collection is None:
            continue
        for obj in list(collection.all_objects):
            if obj.get("aegis_v3_managed"):
                bpy.data.objects.remove(obj, do_unlink=True)
            elif len(obj.users_collection) == 1:
                bpy.context.scene.collection.objects.link(obj)
        bpy.data.collections.remove(collection)


def remove_factory_defaults() -> None:
    for name in ("Cube", "Camera", "Light"):
        obj = bpy.data.objects.get(name)
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)
    collection = bpy.data.collections.get("Collection")
    if collection is not None and not collection.objects and not collection.children:
        bpy.data.collections.remove(collection)


def make_collection(name: str) -> bpy.types.Collection:
    collection = bpy.data.collections.new(name)
    collection["aegis_v3_managed"] = True
    bpy.context.scene.collection.children.link(collection)
    return collection


def mark_managed(obj: bpy.types.Object) -> None:
    obj["aegis_v3_managed"] = True


def set_smooth(obj: bpy.types.Object) -> None:
    if obj.type == "MESH":
        for polygon in obj.data.polygons:
            polygon.use_smooth = True


def make_material(name: str, specification: dict) -> bpy.types.Material:
    material = bpy.data.materials.get(name) or bpy.data.materials.new(name=name)
    # Review materials are swapped by name in a later headless render pass.  Keep
    # every configured material in the .blend even when it is not assigned to a
    # mesh at save time (Blender otherwise drops zero-user datablocks).
    material.use_fake_user = True
    material.diffuse_color = tuple(specification["base"])
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    shader = nodes.new(type="ShaderNodeBsdfPrincipled")
    shader.name = "Principled BSDF"
    output = nodes.new(type="ShaderNodeOutputMaterial")
    output.name = "Material Output"
    material.node_tree.links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    shader.inputs["Base Color"].default_value = tuple(specification["base"])
    shader.inputs["Roughness"].default_value = specification.get("roughness", 0.5)
    shader.inputs["Metallic"].default_value = specification.get("metallic", 0.0)
    emission = specification.get("emission")
    if emission is not None:
        emission_input = shader.inputs.get("Emission Color") or shader.inputs.get("Emission")
        if emission_input is not None:
            emission_input.default_value = tuple(emission)
        strength_input = shader.inputs.get("Emission Strength")
        if strength_input is not None:
            strength_input.default_value = specification.get("emission_strength", 1.0)
    material["aegis_v3_managed"] = True
    return material


def assign_part_materials(
    obj: bpy.types.Object,
    part_name: str,
    materials: dict[str, bpy.types.Material],
) -> None:
    clay_name, design_name = cfg.PART_MATERIALS[part_name]
    obj.data.materials.clear()
    obj.data.materials.append(materials[clay_name])
    obj["clay_material"] = clay_name
    obj["design_material"] = design_name


def add_editable_modifiers(
    obj: bpy.types.Object,
    thickness: float,
    bevel_width: float = 0.0011,
    subdivision: int = 0,
) -> None:
    if thickness > 0.0:
        solidify = obj.modifiers.new(name="V3 Panel Thickness", type="SOLIDIFY")
        solidify.thickness = thickness
        solidify.offset = -0.15
    if bevel_width > 0.0:
        bevel = obj.modifiers.new(name="V3 Edge Softening", type="BEVEL")
        bevel.width = bevel_width
        bevel.segments = 2
        bevel.limit_method = "ANGLE"
    if subdivision > 0:
        modifier = obj.modifiers.new(name="V3 Controlled Subdivision", type="SUBSURF")
        modifier.levels = subdivision
        modifier.render_levels = subdivision
        modifier.subdivision_type = "CATMULL_CLARK"


def set_origin_preserve_world_geometry(
    obj: bpy.types.Object,
    world_origin: tuple[float, float, float] | Vector,
) -> None:
    """Move a mesh origin while keeping its current world-space vertices fixed."""
    origin = Vector(world_origin)
    inverse = obj.matrix_world.inverted()
    local_origin = inverse @ origin
    obj.data.transform(Matrix.Translation(-local_origin))
    obj.matrix_world.translation = origin


def parent_to_root(obj: bpy.types.Object, root: bpy.types.Object) -> None:
    world_matrix = obj.matrix_world.copy()
    obj.parent = root
    obj.matrix_world = world_matrix


def signed_angle(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


def smooth_values(values: list[float], passes: int) -> list[float]:
    result = list(values)
    for _ in range(passes):
        source = list(result)
        for index in range(1, len(result) - 1):
            result[index] = 0.25 * source[index - 1] + 0.5 * source[index] + 0.25 * source[index + 1]
    return result


def interpolate(samples: list[float], t: float) -> float:
    if t <= 0.0:
        return samples[0]
    if t >= 1.0:
        return samples[-1]
    position = t * (len(samples) - 1)
    low = int(math.floor(position))
    high = min(len(samples) - 1, low + 1)
    factor = position - low
    return samples[low] * (1.0 - factor) + samples[high] * factor


class EnvelopeModel:
    def __init__(self, measurements: dict):
        front_profiles = measurements["views"]["front"]["profiles"]
        right_profiles = measurements["views"]["right"]["profiles"]
        left_profiles = measurements["views"]["left"]["profiles"]

        width_values = [float(sample["width_fraction"]) for sample in front_profiles]
        # The front mask includes protruding ear pods around the eye line. The
        # shared shell cage must follow the body silhouette, while EarCover is
        # rebuilt as an embedded independent part later.
        for index, sample in enumerate(front_profiles):
            t = float(sample["t_from_top"])
            if 0.34 <= t <= 0.61:
                cap = 0.875 + 0.18 * abs(t - 0.475)
                width_values[index] = min(width_values[index], cap)
            # Preserve the reference-driven crown and jaw taper, but replace
            # the ear-contaminated circular midsection with a controlled
            # temple/side-wall plateau. This is a profile-conditioning step on
            # measured data, not an ellipsoid radius adjustment.
            if 0.285 <= t <= 0.585:
                width_values[index] = max(width_values[index], 0.885)
        width_values = smooth_values(width_values, cfg.FIT["profile_smoothing_passes"])
        maximum_width = max(width_values)
        self.width_fractions = [value / maximum_width for value in width_values]

        right_depth = [float(sample["width_fraction"]) for sample in right_profiles]
        left_depth = [float(sample["width_fraction"]) for sample in left_profiles]
        depth_values = [(right + left) * 0.5 for right, left in zip(right_depth, left_depth)]
        for index, sample in enumerate(right_profiles):
            t = float(sample["t_from_top"])
            if 0.30 <= t <= 0.58:
                depth_values[index] = max(depth_values[index], 0.965)
        depth_values = smooth_values(depth_values, cfg.FIT["profile_smoothing_passes"])
        maximum_depth = max(depth_values)
        self.depth_fractions = [value / maximum_depth for value in depth_values]

        center_shifts = []
        for right, left in zip(right_profiles, left_profiles):
            right_center = 0.5 * (float(right["left_norm"]) + float(right["right_norm"]))
            left_center_mirrored = 1.0 - 0.5 * (float(left["left_norm"]) + float(left["right_norm"]))
            center_shifts.append(0.5 * (right_center + left_center_mirrored) - 0.5)
        self.center_shifts = smooth_values(center_shifts, cfg.FIT["profile_smoothing_passes"])
        self.ring_count = cfg.FIT["ring_count"]
        self.ring_vertices = cfg.FIT["ring_vertices"]
        self.top_z = cfg.FIT["top_z"]
        self.bottom_z = cfg.FIT["bottom_z"]
        self.height = self.top_z - self.bottom_z

    def exponent(self, t: float) -> float:
        if t < 0.30:
            return cfg.FIT["superellipse_crown"]
        if t < 0.70:
            return cfg.FIT["superellipse_mid"]
        return cfg.FIT["superellipse_lower"]

    def section(self, t: float, inner: bool = False) -> dict[str, float]:
        width = max(
            cfg.FIT["minimum_half_width"] * 2.0,
            cfg.FIT["max_body_width"] * interpolate(self.width_fractions, t),
        )
        depth = max(
            cfg.FIT["minimum_half_depth"] * 2.0,
            cfg.FIT["reference_depth"] * interpolate(self.depth_fractions, t),
        )
        center_y = (
            cfg.FIT["front_bias_y"]
            + interpolate(self.center_shifts, t) * cfg.FIT["reference_depth"] * 0.42
        )
        half_width = width * 0.5
        front_radius = depth * 0.5
        rear_radius = depth * 0.5
        z = self.top_z - t * self.height
        if inner:
            half_width = max(0.010, half_width - cfg.FIT["inner_clearance_x"])
            front_radius = max(0.012, front_radius - cfg.FIT["inner_clearance_front"])
            rear_radius = max(0.012, rear_radius - cfg.FIT["inner_clearance_rear"])
            z -= cfg.FIT["inner_clearance_top"] * (1.0 - t) ** 3
        return {
            "half_width": half_width,
            "front_y": center_y - front_radius,
            "rear_y": center_y + rear_radius,
            "center_y": center_y,
            "z": z,
            "exponent": self.exponent(t),
        }

    def point(self, t: float, phi: float, inner: bool = False) -> Vector:
        section = self.section(t, inner=inner)
        exponent = section["exponent"]
        power = 2.0 / exponent
        cosine = math.cos(phi)
        sine = math.sin(phi)
        x = section["half_width"] * math.copysign(abs(cosine) ** power, cosine)
        sine_shape = abs(sine) ** power
        if sine < 0.0:
            y = section["center_y"] + (section["front_y"] - section["center_y"]) * sine_shape
        else:
            y = section["center_y"] + (section["rear_y"] - section["center_y"]) * sine_shape
        if inner and sine < 0.0:
            # The removable faceplate replaces the ordinary front shell. Shape
            # a smooth internal nose/mouth support cavity only inside its
            # angular footprint; this preserves temple wall thickness while
            # providing real nasal clearance.
            vertical = max(0.0, 1.0 - abs(t - 0.58) / 0.22)
            vertical = vertical * vertical * (3.0 - 2.0 * vertical)
            delta = abs(signed_angle(phi + math.pi * 0.5))
            angular = max(0.0, 1.0 - delta / max(math.radians(1.0), faceplate_half_angle(t) * 0.92))
            angular = angular * angular * (3.0 - 2.0 * angular)
            y -= cfg.FIT["inner_face_support_bulge"] * vertical * angular
        return Vector((x, y, section["z"]))

    def normal(self, t: float, phi: float, inner: bool = False) -> Vector:
        epsilon_t = 0.001
        epsilon_phi = 0.002
        before_t = self.point(max(0.0, t - epsilon_t), phi, inner)
        after_t = self.point(min(1.0, t + epsilon_t), phi, inner)
        before_phi = self.point(t, phi - epsilon_phi, inner)
        after_phi = self.point(t, phi + epsilon_phi, inner)
        tangent_t = after_t - before_t
        tangent_phi = after_phi - before_phi
        normal = tangent_phi.cross(tangent_t).normalized()
        radial = self.point(t, phi, inner) - Vector((0.0, self.section(t, inner)["center_y"], self.section(t, inner)["z"]))
        if normal.dot(radial) < 0.0:
            normal.negate()
        return normal

    def snapshot(self) -> dict:
        return {
            "width_fractions": [round(value, 6) for value in self.width_fractions],
            "depth_fractions": [round(value, 6) for value in self.depth_fractions],
            "center_shifts": [round(value, 6) for value in self.center_shifts],
            "top_z": self.top_z,
            "bottom_z": self.bottom_z,
            "max_body_width": cfg.FIT["max_body_width"],
            "reference_depth": cfg.FIT["reference_depth"],
        }


def create_mesh_object(
    name: str,
    vertices: list[Vector | tuple[float, float, float]],
    faces: list[tuple[int, ...]],
    collection: bpy.types.Collection,
) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    mesh.from_pydata([tuple(vertex) for vertex in vertices], [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    mark_managed(obj)
    set_smooth(obj)
    return obj


def create_full_envelope(
    name: str,
    model: EnvelopeModel,
    collection: bpy.types.Collection,
    inner: bool = False,
) -> bpy.types.Object:
    vertices = []
    faces = []
    rings = model.ring_count
    segments = model.ring_vertices
    for ring_index in range(rings):
        t = ring_index / (rings - 1)
        for segment_index in range(segments):
            phi = 2.0 * math.pi * segment_index / segments
            vertices.append(model.point(t, phi, inner=inner))
    for ring_index in range(rings - 1):
        current = ring_index * segments
        following_ring = (ring_index + 1) * segments
        for segment_index in range(segments):
            following = (segment_index + 1) % segments
            faces.append(
                (
                    current + segment_index,
                    current + following,
                    following_ring + following,
                    following_ring + segment_index,
                )
            )
    faces.append(tuple(reversed(range(segments))))
    return create_mesh_object(name, vertices, faces, collection)


def faceplate_half_angle(t: float) -> float:
    keys = (
        (0.18, math.radians(20.0)),
        (0.28, math.radians(34.0)),
        (0.46, math.radians(35.0)),
        (0.64, math.radians(27.0)),
        (0.86, math.radians(13.0)),
    )
    if t <= keys[0][0]:
        return keys[0][1]
    for (t0, value0), (t1, value1) in zip(keys, keys[1:]):
        if t <= t1:
            factor = (t - t0) / (t1 - t0)
            return value0 * (1.0 - factor) + value1 * factor
    return keys[-1][1]


def faceplate_top_boundary(t: float, phi: float) -> float:
    """Reference-shaped brow: high at the center and lower at the temples."""
    half_angle = max(math.radians(1.0), faceplate_half_angle(max(t, 0.22)))
    edge_ratio = min(1.0, abs(front_delta(phi)) / half_angle)
    return 0.205 + 0.090 * edge_ratio**1.35


def faceplate_bottom_boundary(t: float, phi: float) -> float:
    """Reference-shaped jaw point: low at center, swept upward at the sides."""
    half_angle = max(math.radians(1.0), faceplate_half_angle(min(t, 0.84)))
    edge_ratio = min(1.0, abs(front_delta(phi)) / half_angle)
    return 0.865 - 0.185 * edge_ratio**1.25


def faceplate_contains(t: float, phi: float, angular_padding: float = 0.0) -> bool:
    delta = abs(front_delta(phi))
    half_angle = max(0.0, faceplate_half_angle(t) - angular_padding)
    return (
        delta <= half_angle
        and t >= faceplate_top_boundary(t, phi)
        and t <= faceplate_bottom_boundary(t, phi)
    )


def faceplate_point(model: EnvelopeModel, t: float, phi: float) -> Vector:
    point = model.point(t, phi)
    normal = model.normal(t, phi)
    delta = abs(signed_angle(phi + math.pi * 0.5))
    half_angle = max(0.001, faceplate_half_angle(t))
    center_weight = max(0.0, 1.0 - delta / half_angle) ** 1.55
    vertical_weight = max(0.0, math.sin(math.pi * (t - 0.16) / 0.72))
    offset = (
        cfg.FIT["faceplate_edge_offset"]
        + cfg.FIT["faceplate_center_bulge"] * center_weight * vertical_weight
    )
    return point + normal * offset


def create_surface_patch(
    name: str,
    model: EnvelopeModel,
    collection: bpy.types.Collection,
    classifier: Callable[[float, float], bool],
    point_function: Callable[[float, float], Vector],
) -> bpy.types.Object:
    rings = model.ring_count
    segments = model.ring_vertices
    vertex_map: dict[tuple[int, int], int] = {}
    vertices: list[Vector] = []
    faces: list[tuple[int, int, int, int]] = []

    def mapped_vertex(ring_index: int, segment_index: int) -> int:
        key = (ring_index, segment_index % segments)
        if key not in vertex_map:
            t = ring_index / (rings - 1)
            phi = 2.0 * math.pi * (segment_index % segments) / segments
            vertex_map[key] = len(vertices)
            vertices.append(point_function(t, phi))
        return vertex_map[key]

    for ring_index in range(rings - 1):
        t_mid = (ring_index + 0.5) / (rings - 1)
        for segment_index in range(segments):
            phi_mid = 2.0 * math.pi * (segment_index + 0.5) / segments
            if not classifier(t_mid, phi_mid):
                continue
            faces.append(
                (
                    mapped_vertex(ring_index, segment_index),
                    mapped_vertex(ring_index, segment_index + 1),
                    mapped_vertex(ring_index + 1, segment_index + 1),
                    mapped_vertex(ring_index + 1, segment_index),
                )
            )
    if not faces:
        raise RuntimeError(f"Surface classifier created no faces for {name}")
    # Crown patches meet at the tiny top section of the measured loft. Cap each
    # half so the visible shell is closed instead of exposing a dark top hole.
    if name in {"CrownFront", "CrownRear"}:
        selected = []
        t_mid = 0.5 / (rings - 1)
        for segment_index in range(segments):
            phi_mid = 2.0 * math.pi * (segment_index + 0.5) / segments
            if classifier(t_mid, phi_mid):
                selected.append(segment_index)
        if selected:
            boundary_points = []
            for segment_index in selected:
                boundary_points.append(point_function(0.0, 2.0 * math.pi * segment_index / segments))
                boundary_points.append(point_function(0.0, 2.0 * math.pi * (segment_index + 1) / segments))
            center_index = len(vertices)
            vertices.append(sum(boundary_points, Vector()) / len(boundary_points))
            for segment_index in selected:
                faces.append(
                    (
                        center_index,
                        mapped_vertex(0, segment_index),
                        mapped_vertex(0, segment_index + 1),
                    )
                )
    return create_mesh_object(name, vertices, faces, collection)


def create_parametric_patch(
    name: str,
    collection: bpy.types.Collection,
    u_steps: int,
    v_steps: int,
    point_function: Callable[[float, float], Vector],
    cap_v0: bool = False,
) -> bpy.types.Object:
    """Create a continuous quad patch whose border is analytic, not cell-selected."""
    vertices: list[Vector] = []
    faces: list[tuple[int, ...]] = []
    for v_index in range(v_steps + 1):
        v = v_index / v_steps
        for u_index in range(u_steps + 1):
            u = -1.0 + 2.0 * u_index / u_steps
            vertices.append(point_function(u, v))
    stride = u_steps + 1
    for v_index in range(v_steps):
        for u_index in range(u_steps):
            lower = v_index * stride + u_index
            upper = (v_index + 1) * stride + u_index
            faces.append((lower, lower + 1, upper + 1, upper))
    if cap_v0:
        center = sum(vertices[:stride], Vector()) / stride
        center_index = len(vertices)
        vertices.append(center)
        for u_index in range(u_steps):
            faces.append((center_index, u_index, u_index + 1))
    return create_mesh_object(name, vertices, faces, collection)


def create_faceplate_panel(
    model: EnvelopeModel,
    collection: bpy.types.Collection,
) -> bpy.types.Object:
    def point(u: float, v: float) -> Vector:
        edge = abs(u)
        top_t = 0.205 + 0.090 * edge**1.35
        bottom_t = 0.865 - 0.185 * edge**1.25
        t = top_t * (1.0 - v) + bottom_t * v
        phi = -math.pi * 0.5 + u * faceplate_half_angle(t)
        return faceplate_point(model, t, phi)

    return create_parametric_patch("Faceplate", collection, 40, 42, point)


def create_crown_front_panel(
    model: EnvelopeModel,
    collection: bpy.types.Collection,
) -> bpy.types.Object:
    def point(u: float, v: float) -> Vector:
        phi = -math.pi * 0.5 + u * math.pi * 0.5
        # The brow line is driven by the same normalized boundary used by the
        # faceplate, with a deliberate service gap exposing the inner shell.
        bottom_t = 0.195 + 0.090 * min(1.0, abs(u) / 0.52) ** 1.35
        t = max(0.0, bottom_t - 0.010) * v
        return model.point(t, phi) + model.normal(t, phi) * cfg.FIT["panel_offset"]

    return create_parametric_patch("CrownFront", collection, 48, 20, point, cap_v0=True)


def front_delta(phi: float) -> float:
    return signed_angle(phi + math.pi * 0.5)


def panel_classifiers() -> dict[str, Callable[[float, float], bool]]:
    def is_front(phi: float) -> bool:
        return abs(front_delta(phi)) <= math.pi * 0.5

    def is_right(phi: float) -> bool:
        return math.cos(phi) >= 0.0

    def beyond_faceplate(t: float, phi: float, padding: float = math.radians(2.0)) -> bool:
        if not 0.19 <= t <= 0.88:
            return True
        return abs(front_delta(phi)) >= faceplate_half_angle(t) + padding

    return {
        "CrownFront": lambda t, phi: is_front(phi) and t <= faceplate_top_boundary(t, phi) - 0.010,
        "CrownRear": lambda t, phi: t <= 0.300 and not is_front(phi),
        "RearShell_R": lambda t, phi: 0.325 <= t <= 0.925 and not is_front(phi) and is_right(phi),
        "RearShell_L": lambda t, phi: 0.325 <= t <= 0.925 and not is_front(phi) and not is_right(phi),
        "Temple_R": lambda t, phi: 0.315 <= t <= 0.535 and is_front(phi) and is_right(phi) and beyond_faceplate(t, phi),
        "Temple_L": lambda t, phi: 0.315 <= t <= 0.535 and is_front(phi) and not is_right(phi) and beyond_faceplate(t, phi),
        "Cheek_R": lambda t, phi: 0.565 <= t <= 0.785 and is_front(phi) and is_right(phi) and beyond_faceplate(t, phi),
        "Cheek_L": lambda t, phi: 0.565 <= t <= 0.785 and is_front(phi) and not is_right(phi) and beyond_faceplate(t, phi),
        "Jaw_R": lambda t, phi: 0.805 <= t <= 0.965 and is_front(phi) and is_right(phi) and abs(front_delta(phi)) >= math.radians(17.0),
        "Jaw_L": lambda t, phi: 0.805 <= t <= 0.965 and is_front(phi) and not is_right(phi) and abs(front_delta(phi)) >= math.radians(17.0),
        "Chin": lambda t, phi: 0.885 <= t <= 0.985 and abs(front_delta(phi)) <= math.radians(15.0),
        "Faceplate": lambda t, phi: faceplate_contains(t, phi),
    }


def panel_origin(name: str, model: EnvelopeModel) -> Vector:
    origins = {
        "CrownFront": (0.0, -0.035, 0.112),
        "CrownRear": (0.0, 0.045, 0.112),
        "RearShell_R": (0.085, 0.055, 0.025),
        "RearShell_L": (-0.085, 0.055, 0.025),
        "Temple_R": (0.093, -0.020, 0.045),
        "Temple_L": (-0.093, -0.020, 0.045),
        "Cheek_R": (0.086, -0.055, -0.042),
        "Cheek_L": (-0.086, -0.055, -0.042),
        "Jaw_R": (0.077, -0.040, -0.086),
        "Jaw_L": (-0.077, -0.040, -0.086),
        "Chin": (0.0, -0.105, -0.105),
        "Faceplate": (0.0, -0.108, 0.082),
    }
    return Vector(origins[name])


def create_ellipsoid_proxy(
    name: str,
    location: tuple[float, float, float],
    radii: tuple[float, float, float],
    collection: bpy.types.Collection,
    material: bpy.types.Material,
    segments: int = 28,
    rings: int = 18,
) -> bpy.types.Object:
    bpy.ops.mesh.primitive_uv_sphere_add(segments=segments, ring_count=rings, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.scale = radii
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    for current in list(obj.users_collection):
        current.objects.unlink(obj)
    collection.objects.link(obj)
    mark_managed(obj)
    obj.data.materials.append(material)
    set_smooth(obj)
    return obj


def build_head_proxy(collection: bpy.types.Collection, material: bpy.types.Material) -> list[bpy.types.Object]:
    head = cfg.HEAD
    objects = [
        create_ellipsoid_proxy("HeadProxyV3_Cranium", head["cranium_center"], head["cranium_radii"], collection, material),
        create_ellipsoid_proxy("HeadProxyV3_Face", head["face_center"], head["face_radii"], collection, material, 24, 16),
        create_ellipsoid_proxy("HeadProxyV3_Jaw", head["jaw_center"], head["jaw_radii"], collection, material, 24, 14),
        create_ellipsoid_proxy("HeadProxyV3_Chin", head["chin_center"], head["chin_radii"], collection, material, 20, 12),
        create_ellipsoid_proxy("HeadProxyV3_NoseClearance", head["nose_center"], head["nose_radii"], collection, material, 18, 12),
    ]
    for side, sign in (("L", -1.0), ("R", 1.0)):
        ear_x, ear_y, ear_z = head["ear_center"]
        objects.append(
            create_ellipsoid_proxy(
                f"HeadProxyV3_Ear_{side}",
                (sign * ear_x, ear_y, ear_z),
                head["ear_radii"],
                collection,
                material,
                18,
                12,
            )
        )
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=28,
        radius=head["neck_radius"],
        depth=head["neck_depth"],
        location=head["neck_center"],
    )
    neck = bpy.context.object
    neck.name = "HeadProxyV3_Neck"
    for current in list(neck.users_collection):
        current.objects.unlink(neck)
    collection.objects.link(neck)
    mark_managed(neck)
    neck.data.materials.append(material)
    bevel = neck.modifiers.new(name="Proxy Neck Softening", type="BEVEL")
    bevel.width = 0.004
    bevel.segments = 3
    set_smooth(neck)
    objects.append(neck)
    return objects


def create_ear_cover(
    name: str,
    side_sign: float,
    model: EnvelopeModel,
    collection: bpy.types.Collection,
) -> tuple[bpy.types.Object, Vector]:
    t = 0.50
    phi = 0.0 if side_sign > 0.0 else math.pi
    surface = model.point(t, phi)
    outward = Vector((side_sign, 0.0, 0.0))
    center = surface + outward * 0.001
    # A stepped, partly embedded coaxial profile. The broad flange sits into
    # the side shell; only the smaller service cap projects outward.
    axial_offsets = (-0.007, -0.004, -0.001, 0.0035, 0.0075, 0.0105)
    radii = (
        cfg.FIT["ear_radius"] * 0.76,
        cfg.FIT["ear_radius"] * 0.98,
        cfg.FIT["ear_radius"] * 1.08,
        cfg.FIT["ear_radius"] * 0.94,
        cfg.FIT["ear_radius"] * 0.82,
        cfg.FIT["ear_face_radius"],
    )
    segments = 48
    vertices = []
    for axial, radius in zip(axial_offsets, radii):
        ring_center = center + outward * axial
        for index in range(segments):
            angle = 2.0 * math.pi * index / segments
            vertices.append(
                (
                    ring_center.x,
                    ring_center.y + radius * math.cos(angle),
                    ring_center.z + radius * math.sin(angle),
                )
            )
    faces = []
    for ring_index in range(len(axial_offsets) - 1):
        start = ring_index * segments
        next_start = (ring_index + 1) * segments
        for index in range(segments):
            following = (index + 1) % segments
            faces.append((start + index, start + following, next_start + following, next_start + index))
    faces.append(tuple(reversed(range(segments))))
    faces.append(tuple(range((len(axial_offsets) - 1) * segments, len(axial_offsets) * segments)))
    if side_sign < 0.0:
        faces = [tuple(reversed(face)) for face in faces]
    obj = create_mesh_object(name, vertices, faces, collection)
    return obj, center


def create_ear_emitter(
    name: str,
    side_sign: float,
    center: Vector,
    collection: bpy.types.Collection,
) -> tuple[bpy.types.Object, Vector]:
    location = center + Vector((side_sign * 0.0132, -0.0005, 0.0))
    bpy.ops.mesh.primitive_cube_add(location=location)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = (0.0035, 0.0046, 0.028)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    for current in list(obj.users_collection):
        current.objects.unlink(obj)
    collection.objects.link(obj)
    mark_managed(obj)
    set_smooth(obj)
    add_editable_modifiers(obj, 0.0, 0.0017, 0)
    return obj, location


def create_neck_ring_segment(
    name: str,
    angle_start: float,
    angle_end: float,
    collection: bpy.types.Collection,
) -> tuple[bpy.types.Object, Vector]:
    outer_x = cfg.FIT["neck_ring_outer_x"]
    outer_y = cfg.FIT["neck_ring_outer_y"]
    inner_x = outer_x - cfg.FIT["neck_ring_radial_width"]
    inner_y = outer_y - cfg.FIT["neck_ring_radial_width"]
    bottom_z = cfg.FIT["neck_ring_z"] - cfg.FIT["neck_ring_height"] * 0.5
    top_z = cfg.FIT["neck_ring_z"] + cfg.FIT["neck_ring_height"] * 0.5
    segments = 28
    vertices = []
    for index in range(segments + 1):
        angle = angle_start + (angle_end - angle_start) * index / segments
        vertices.extend(
            (
                (outer_x * math.cos(angle), outer_y * math.sin(angle), bottom_z),
                (outer_x * math.cos(angle), outer_y * math.sin(angle), top_z),
                (inner_x * math.cos(angle), inner_y * math.sin(angle), bottom_z),
                (inner_x * math.cos(angle), inner_y * math.sin(angle), top_z),
            )
        )
    faces = []
    for index in range(segments):
        start = index * 4
        following = (index + 1) * 4
        faces.extend(
            (
                (start, following, following + 1, start + 1),
                (start + 3, following + 3, following + 2, start + 2),
                (start + 1, following + 1, following + 3, start + 3),
                (start + 2, following + 2, following, start),
            )
        )
    faces.extend(((0, 1, 3, 2), (segments * 4 + 2, segments * 4 + 3, segments * 4 + 1, segments * 4)))
    obj = create_mesh_object(name, vertices, faces, collection)
    middle_angle = (angle_start + angle_end) * 0.5
    origin = Vector((0.0, outer_y * math.sin(middle_angle) * 0.65, cfg.FIT["neck_ring_z"]))
    return obj, origin


def eye_parameter_polygon(side_sign: float, inner: bool = False) -> list[tuple[float, float]]:
    scale = 0.72 if inner else 1.0
    width = cfg.FIT["eye_width"] * scale
    height = cfg.FIT["eye_height"] * scale
    half_width = width * 0.5
    half_height = height * 0.5
    bevel = min(half_width * 0.18, half_height * 0.65)
    points = (
        (-half_width + bevel, -half_height),
        (half_width - bevel, -half_height),
        (half_width, -half_height + bevel),
        (half_width, half_height - bevel),
        (half_width - bevel, half_height),
        (-half_width + bevel, half_height),
        (-half_width, half_height - bevel),
        (-half_width, -half_height + bevel),
    )
    return [(side_sign * outward, vertical) for outward, vertical in points]


def eye_surface_point(model: EnvelopeModel, side_sign: float, outward_x: float, vertical_z: float, inset: float) -> Vector:
    center_delta = side_sign * 0.46
    section = model.section(0.43)
    angular_offset = outward_x / max(0.04, section["half_width"])
    phi = -math.pi * 0.5 + center_delta + angular_offset
    slope = math.tan(math.radians(cfg.FIT["eye_angle_degrees"]))
    outward_direction = 1.0 if side_sign * outward_x >= 0.0 else -1.0
    raised = slope * abs(outward_x) * outward_direction
    t = 0.43 - (vertical_z + raised) / model.height
    base = faceplate_point(model, t, phi)
    return base + model.normal(t, phi) * inset


def create_eye_parts(
    side: str,
    side_sign: float,
    model: EnvelopeModel,
    collection: bpy.types.Collection,
) -> tuple[bpy.types.Object, bpy.types.Object, Vector]:
    outer_uv = eye_parameter_polygon(side_sign, inner=False)
    inner_uv = eye_parameter_polygon(side_sign, inner=True)
    # The lenses are mounted onto the powered faceplate, not cut through it.
    # Keep the housing proud of the faceplate and recess the lens only relative
    # to that housing, while remaining outside the armor surface.
    outer = [eye_surface_point(model, side_sign, x, z, 0.0034) for x, z in outer_uv]
    inner = [eye_surface_point(model, side_sign, x, z, 0.0032) for x, z in inner_uv]
    count = len(outer)
    housing_faces = []
    housing_vertices = outer + inner
    for index in range(count):
        following = (index + 1) % count
        housing_faces.append((index, following, count + following, count + index))
    housing = create_mesh_object(f"EyeHousing_{side}", housing_vertices, housing_faces, collection)
    add_editable_modifiers(housing, 0.0018, 0.00045)
    lens_vertices = [eye_surface_point(model, side_sign, x, z, cfg.FIT["eye_inset"]) for x, z in inner_uv]
    lens = create_mesh_object(f"EyeLens_{side}", lens_vertices, [tuple(range(count))], collection)
    add_editable_modifiers(lens, 0.0014, 0.00035)
    center = eye_surface_point(model, side_sign, 0.0, 0.0, 0.0)
    return housing, lens, center


def create_reference_empties(collection: bpy.types.Collection) -> list[str]:
    warnings = []
    specifications = {
        "front": ((0.0, 0.175, 0.005), (math.pi * 0.5, 0.0, 0.0)),
        "right": ((-0.175, 0.0, 0.005), (math.pi * 0.5, 0.0, math.pi * 0.5)),
        "left": ((0.175, 0.0, 0.005), (math.pi * 0.5, 0.0, math.pi * 0.5)),
        "top": ((0.0, 0.0, -0.175), (0.0, 0.0, 0.0)),
    }
    for view, (location, rotation) in specifications.items():
        path = cfg.DERIVED_REFERENCE_DIR / f"{view}_crop.png"
        try:
            image = bpy.data.images.load(str(path), check_existing=True)
            # Blender 5.x no longer accepts Image as the datablock argument to
            # bpy.data.objects.new(). Create a true Image Empty through the
            # operator, then relink it into the managed reference collection.
            bpy.ops.object.empty_add(type="IMAGE")
            obj = bpy.context.object
            obj.name = f"ReferenceV3_{view.title()}"
            obj.data = image
            for current in list(obj.users_collection):
                current.objects.unlink(obj)
            collection.objects.link(obj)
            obj.empty_display_size = 0.32
            obj.color[3] = 0.42
            obj.location = location
            obj.rotation_euler = rotation
            obj.hide_render = True
            mark_managed(obj)
        except Exception as error:
            warnings.append(f"Reference empty {view} failed: {error}")
    return warnings


def look_at(obj: bpy.types.Object, target: tuple[float, float, float]) -> None:
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def build_render_rig(collection: bpy.types.Collection) -> None:
    for _, (name, location, camera_type) in cfg.CAMERAS.items():
        data = bpy.data.cameras.new(name)
        camera = bpy.data.objects.new(name, data)
        collection.objects.link(camera)
        mark_managed(camera)
        camera.location = location
        data.type = camera_type
        if camera_type == "ORTHO":
            data.ortho_scale = cfg.RENDER["ortho_scale"]
        else:
            data.lens = cfg.RENDER["lens"]
        data.dof.use_dof = False
        look_at(camera, cfg.RENDER["target"])
    for name, location, energy, size in cfg.RENDER["lights"]:
        data = bpy.data.lights.new(name=name, type="AREA")
        data.energy = energy
        data.shape = "DISK"
        data.size = size
        light = bpy.data.objects.new(name, data)
        collection.objects.link(light)
        mark_managed(light)
        light.location = location
        look_at(light, cfg.RENDER["target"])


def clearance_metrics(model: EnvelopeModel) -> dict:
    head = cfg.HEAD
    top_clearance = model.section(0.0, inner=True)["z"] - (
        head["cranium_center"][2] + head["cranium_radii"][2]
    )
    temple_section = model.section(0.46, inner=True)
    side_clearance = temple_section["half_width"] - head["cranium_radii"][0]
    rear_clearance = temple_section["rear_y"] - (head["cranium_center"][1] + head["cranium_radii"][1])
    face_section = model.section(0.58, inner=True)
    nose_tip = head["nose_center"][1] - head["nose_radii"][1]
    inner_nose_y = model.point(0.58, -math.pi * 0.5, inner=True).y
    faceplate_nose_y = faceplate_point(model, 0.58, -math.pi * 0.5).y
    nose_clearance = nose_tip - inner_nose_y
    faceplate_nose_clearance = nose_tip - faceplate_nose_y
    ear_x = head["ear_center"][0] + head["ear_radii"][0]
    ear_clearance = temple_section["half_width"] - ear_x
    values = {
        "top": top_clearance,
        "temple_side": side_clearance,
        "rear": rear_clearance,
        "nose": nose_clearance,
        "ear": ear_clearance,
    }
    return {
        "samples_m": {key: round(value, 6) for key, value in values.items()},
        "faceplate_nose_clearance_m": round(faceplate_nose_clearance, 6),
        "inner_nose_surface_y_m": round(inner_nose_y, 6),
        "faceplate_nose_surface_y_m": round(faceplate_nose_y, 6),
        "minimum_sample_m": round(min(values.values()), 6),
        "penetration_detected": any(value < 0.0 for value in values.values()),
        "target_general_m": [0.008, 0.018],
        "target_local_minimum_m": 0.004,
    }


def faceplate_curvature_metrics(model: EnvelopeModel) -> dict:
    samples = {}
    for label, t in (("forehead", 0.24), ("eye_line", 0.43), ("nose_mouth", 0.63), ("chin", 0.81)):
        half = faceplate_half_angle(t) * 0.90
        center = faceplate_point(model, t, -math.pi * 0.5)
        left = faceplate_point(model, t, -math.pi * 0.5 - half)
        right = faceplate_point(model, t, -math.pi * 0.5 + half)
        edge_y = 0.5 * (left.y + right.y)
        samples[label] = {
            "t_from_top": t,
            "z_m": round(center.z, 6),
            "center_y_m": round(center.y, 6),
            "edge_average_y_m": round(edge_y, 6),
            "center_forward_of_edges_m": round(edge_y - center.y, 6),
        }
    return samples


def validate_scene(root: bpy.types.Object, envelope_only: bool) -> None:
    if root.location.length > 1.0e-8 or any(abs(value) > 1.0e-8 for value in root.rotation_euler):
        raise RuntimeError("HelmetRoot is not at the origin")
    if any(abs(value - 1.0) > 1.0e-8 for value in root.scale):
        raise RuntimeError("HelmetRoot scale is not identity")
    if not envelope_only:
        missing = [name for name in cfg.REQUIRED_EXPORT_PARTS if bpy.data.objects.get(name) is None]
        if missing:
            raise RuntimeError(f"Missing required v3 parts: {missing}")
        zero_origins = [
            obj.name
            for obj in bpy.data.collections["HELMET_V3"].all_objects
            if obj.type == "MESH" and obj.name != "InnerShell" and obj.location.length < 1.0e-5
        ]
        if zero_origins:
            raise RuntimeError(f"Parts still have world-origin pivots: {zero_origins}")
    for obj in bpy.data.objects:
        if any(component < 0.0 for component in obj.scale):
            raise RuntimeError(f"Negative scale on {obj.name}")


def main() -> None:
    arguments = parse_args()
    started = time.perf_counter()
    ensure_directories()
    if not cfg.MEASUREMENTS_PATH.is_file():
        raise FileNotFoundError(f"Run prepare_helmet_references_v3.py first: {cfg.MEASUREMENTS_PATH}")
    measurements = json.loads(cfg.MEASUREMENTS_PATH.read_text(encoding="utf-8"))

    remove_managed_collections()
    remove_factory_defaults()
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0
    scene.render.film_transparent = False

    collections = {name: make_collection(name) for name in cfg.MANAGED_COLLECTIONS}
    materials = {name: make_material(name, specification) for name, specification in cfg.MATERIALS.items()}
    reference_warnings = create_reference_empties(collections["REFERENCE_V3"])
    build_head_proxy(collections["HEAD_PROXY_V3"], materials["Proxy"])
    build_render_rig(collections["RENDER_V3"])

    model = EnvelopeModel(measurements)
    root = bpy.data.objects.new("HelmetRoot", None)
    collections["HELMET_V3"].objects.link(root)
    mark_managed(root)
    root.empty_display_type = "PLAIN_AXES"
    root.empty_display_size = 0.035

    master = create_full_envelope("HelmetEnvelope_Master", model, collections["MODELING_V3"])
    master.data.materials.append(materials["Silhouette"])
    add_editable_modifiers(master, 0.0, 0.0005, cfg.FIT["master_subdivision_levels"])
    master.display_type = "WIRE"
    master.hide_render = not arguments.envelope_only

    built_parts = []
    if not arguments.envelope_only:
        inner = create_full_envelope("InnerShell", model, collections["HELMET_V3"], inner=True)
        assign_part_materials(inner, "InnerShell", materials)
        add_editable_modifiers(inner, 0.0030, 0.0008, 0)
        set_origin_preserve_world_geometry(inner, (0.0, 0.0, 0.010))
        parent_to_root(inner, root)
        built_parts.append(inner)

        classifiers = panel_classifiers()
        for name in (
            "CrownFront",
            "CrownRear",
            "RearShell_L",
            "RearShell_R",
            "Temple_L",
            "Temple_R",
            "Cheek_L",
            "Cheek_R",
            "Jaw_L",
            "Jaw_R",
            "Chin",
            "Faceplate",
        ):
            if name == "Faceplate":
                part = create_faceplate_panel(model, collections["HELMET_V3"])
            elif name == "CrownFront":
                part = create_crown_front_panel(model, collections["HELMET_V3"])
            else:
                rear_service_shift = (
                    Vector((0.0, 0.0030, 0.0))
                    if name in {"CrownRear", "RearShell_L", "RearShell_R"}
                    else Vector()
                )
                point_function = lambda t, phi: (
                    model.point(t, phi)
                    + model.normal(t, phi) * cfg.FIT["panel_offset"]
                    + rear_service_shift
                )
                part = create_surface_patch(
                    name,
                    model,
                    collections["HELMET_V3"],
                    classifiers[name],
                    point_function,
                )
            assign_part_materials(part, name, materials)
            thickness = cfg.FIT["faceplate_thickness"] if name == "Faceplate" else cfg.FIT["panel_thickness"]
            # The analytic faceplate boundary already carries its designed
            # curvature. A bevel on this strongly curved open rim produces
            # self-intersecting miters, so retain real thickness but no fake
            # edge-rounding there.
            bevel_width = 0.0 if name in {"Faceplate", "CrownFront", "CrownRear"} else 0.00075
            controlled_subdivision = 1 if name == "Faceplate" else 0
            add_editable_modifiers(part, thickness, bevel_width, controlled_subdivision)
            set_origin_preserve_world_geometry(part, panel_origin(name, model))
            parent_to_root(part, root)
            built_parts.append(part)

        for side, sign in (("L", -1.0), ("R", 1.0)):
            ear, center = create_ear_cover(f"EarCover_{side}", sign, model, collections["HELMET_V3"])
            assign_part_materials(ear, f"EarCover_{side}", materials)
            add_editable_modifiers(ear, 0.0, 0.0010, 0)
            set_origin_preserve_world_geometry(ear, center)
            parent_to_root(ear, root)
            built_parts.append(ear)

            emitter, emitter_center = create_ear_emitter(
                f"EarEmitter_{side}", sign, center, collections["HELMET_V3"]
            )
            assign_part_materials(emitter, f"EarEmitter_{side}", materials)
            set_origin_preserve_world_geometry(emitter, emitter_center)
            parent_to_root(emitter, root)
            built_parts.append(emitter)

            housing, lens, eye_center = create_eye_parts(side, sign, model, collections["HELMET_V3"])
            assign_part_materials(housing, f"EyeHousing_{side}", materials)
            assign_part_materials(lens, f"EyeLens_{side}", materials)
            set_origin_preserve_world_geometry(housing, eye_center)
            set_origin_preserve_world_geometry(lens, eye_center)
            parent_to_root(housing, root)
            parent_to_root(lens, root)
            built_parts.extend((housing, lens))

        front_ring, front_origin = create_neck_ring_segment("NeckRingFront", math.pi, 2.0 * math.pi, collections["HELMET_V3"])
        rear_ring, rear_origin = create_neck_ring_segment("NeckRingRear", 0.0, math.pi, collections["HELMET_V3"])
        for ring, origin in ((front_ring, front_origin), (rear_ring, rear_origin)):
            assign_part_materials(ring, ring.name, materials)
            add_editable_modifiers(ring, 0.0, 0.0008, 0)
            set_origin_preserve_world_geometry(ring, origin)
            parent_to_root(ring, root)
            built_parts.append(ring)

    validate_scene(root, arguments.envelope_only)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=str(cfg.BLEND_PATH))

    elapsed = time.perf_counter() - started
    metrics = {
        "script": str(Path(__file__).resolve()),
        "iteration": arguments.iteration,
        "envelope_only": arguments.envelope_only,
        "elapsed_seconds": round(elapsed, 4),
        "blend_path": str(cfg.BLEND_PATH),
        "built_part_count": len(built_parts),
        "reference_warnings": reference_warnings,
        "fitted_profiles": model.snapshot(),
        "clearance": clearance_metrics(model),
        "faceplate_curvature": faceplate_curvature_metrics(model),
    }
    log_path = cfg.LOG_DIR / f"iteration_{arguments.iteration}_build.json"
    log_path.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"AEGIS_V3_BUILD_OK iteration={arguments.iteration} envelope_only={arguments.envelope_only} "
        f"parts={len(built_parts)} elapsed={elapsed:.3f}s"
    )
    print(f"AEGIS_V3_BLEND={cfg.BLEND_PATH}")


if __name__ == "__main__":
    main()
