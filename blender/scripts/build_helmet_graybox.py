"""Build the editable AEGIS-R7 science-fiction helmet graybox."""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import bpy
from mathutils import Vector


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import helmet_config as cfg


def ensure_directories() -> None:
    for path in (
        cfg.HELMET_ONLY_DIR,
        cfg.WITH_HEAD_DIR,
        cfg.LOG_DIR,
        cfg.WORK_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)


def remove_managed_collections() -> None:
    for collection_name in cfg.MANAGED_COLLECTIONS:
        collection = bpy.data.collections.get(collection_name)
        if collection is None:
            continue
        for obj in list(collection.all_objects):
            if obj.get("aegis_managed"):
                bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.collections.remove(collection)


def remove_factory_defaults_if_untouched() -> None:
    objects = set(bpy.data.objects.keys())
    if objects == {"Camera", "Cube", "Light"}:
        for name in tuple(objects):
            bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)
        default_collection = bpy.data.collections.get("Collection")
        if default_collection and not default_collection.objects:
            bpy.data.collections.remove(default_collection)


def make_collection(name: str) -> bpy.types.Collection:
    collection = bpy.data.collections.new(name)
    collection["aegis_managed"] = True
    bpy.context.scene.collection.children.link(collection)
    return collection


def move_to_collection(obj: bpy.types.Object, collection: bpy.types.Collection) -> None:
    for current in list(obj.users_collection):
        current.objects.unlink(obj)
    collection.objects.link(obj)
    obj["aegis_managed"] = True


def set_smooth(obj: bpy.types.Object) -> None:
    if obj.type == "MESH":
        for polygon in obj.data.polygons:
            polygon.use_smooth = True


def add_bevel(obj: bpy.types.Object, width: float, segments: int = 3) -> None:
    bevel = obj.modifiers.new(name="Edge Softening", type="BEVEL")
    bevel.width = width
    bevel.segments = segments
    bevel.limit_method = "ANGLE"


def make_material(name: str, spec: dict) -> bpy.types.Material:
    material = bpy.data.materials.get(name) or bpy.data.materials.new(name=name)
    color = tuple(spec["color"])
    material.diffuse_color = color
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    principled = nodes.new(type="ShaderNodeBsdfPrincipled")
    principled.name = "Principled BSDF"
    output = nodes.new(type="ShaderNodeOutputMaterial")
    output.name = "Material Output"
    material.node_tree.links.new(principled.outputs["BSDF"], output.inputs["Surface"])
    principled.inputs["Base Color"].default_value = color
    principled.inputs["Roughness"].default_value = spec["roughness"]
    principled.inputs["Metallic"].default_value = 0.0
    if "Alpha" in principled.inputs:
        principled.inputs["Alpha"].default_value = color[3]
    if color[3] < 1.0:
        if hasattr(material, "surface_render_method"):
            material.surface_render_method = "DITHERED"
        elif hasattr(material, "blend_method"):
            material.blend_method = "HASHED"
    material["aegis_managed"] = True
    return material


def assign_material(obj: bpy.types.Object, material: bpy.types.Material) -> None:
    obj.data.materials.clear()
    obj.data.materials.append(material)


def parent_to_root(obj: bpy.types.Object, root: bpy.types.Object) -> None:
    obj.parent = root
    obj.matrix_parent_inverse = root.matrix_world.inverted()


def add_uv_ellipsoid(
    name: str,
    location: tuple[float, float, float],
    radii: tuple[float, float, float],
    material: bpy.types.Material,
    collection: bpy.types.Collection,
    segments: int = 28,
    rings: int = 18,
) -> bpy.types.Object:
    bpy.ops.mesh.primitive_uv_sphere_add(segments=segments, ring_count=rings, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.scale = radii
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    move_to_collection(obj, collection)
    assign_material(obj, material)
    set_smooth(obj)
    return obj


def add_cylinder(
    name: str,
    location: tuple[float, float, float],
    radius: float,
    depth: float,
    material: bpy.types.Material,
    collection: bpy.types.Collection,
    axis: str = "Z",
    vertices: int = 32,
    bevel: float = 0.0018,
) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cylinder_add(vertices=vertices, radius=1.0, depth=2.0, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.scale = (radius, radius, depth * 0.5)
    if axis == "X":
        obj.rotation_euler[1] = math.radians(90.0)
    elif axis == "Y":
        obj.rotation_euler[0] = math.radians(90.0)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    move_to_collection(obj, collection)
    assign_material(obj, material)
    set_smooth(obj)
    if bevel:
        add_bevel(obj, bevel, 3)
    return obj


def add_beveled_box(
    name: str,
    location: tuple[float, float, float],
    dimensions: tuple[float, float, float],
    material: bpy.types.Material,
    collection: bpy.types.Collection,
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
    bevel: float = 0.0018,
) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=2.0, location=location, rotation=rotation)
    obj = bpy.context.object
    obj.name = name
    obj.scale = tuple(value * 0.5 for value in dimensions)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    move_to_collection(obj, collection)
    assign_material(obj, material)
    add_bevel(obj, bevel, 4)
    return obj


def create_prism(
    name: str,
    polygon_xz: list[tuple[float, float]],
    y_front: float,
    y_back: float,
    material: bpy.types.Material,
    collection: bpy.types.Collection,
    bevel: float,
) -> bpy.types.Object:
    count = len(polygon_xz)
    vertices = [(x, y_front, z) for x, z in polygon_xz]
    vertices += [(x, y_back, z) for x, z in polygon_xz]
    faces = [tuple(reversed(range(count))), tuple(range(count, count * 2))]
    for index in range(count):
        next_index = (index + 1) % count
        faces.append((index, next_index, count + next_index, count + index))
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    obj["aegis_managed"] = True
    assign_material(obj, material)
    add_bevel(obj, bevel, 3)
    return obj


def create_open_ellipsoid_shell(
    name: str,
    radii: tuple[float, float, float],
    center_z: float,
    theta_max: float,
    material: bpy.types.Material,
    collection: bpy.types.Collection,
    thickness: float,
) -> bpy.types.Object:
    rx, ry, rz = radii
    azimuth_segments = 36
    latitude_segments = 18
    first_theta = 0.12
    vertices = [(0.0, 0.0, center_z + rz)]
    for row in range(latitude_segments + 1):
        theta = first_theta + (theta_max - first_theta) * row / latitude_segments
        for column in range(azimuth_segments):
            phi = 2.0 * math.pi * column / azimuth_segments
            vertices.append(
                (
                    rx * math.sin(theta) * math.cos(phi),
                    ry * math.sin(theta) * math.sin(phi),
                    center_z + rz * math.cos(theta),
                )
            )
    faces = []
    for column in range(azimuth_segments):
        faces.append((0, 1 + column, 1 + (column + 1) % azimuth_segments))
    for row in range(latitude_segments):
        start = 1 + row * azimuth_segments
        next_start = start + azimuth_segments
        for column in range(azimuth_segments):
            next_column = (column + 1) % azimuth_segments
            faces.append(
                (
                    start + column,
                    next_start + column,
                    next_start + next_column,
                    start + next_column,
                )
            )
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    obj["aegis_managed"] = True
    assign_material(obj, material)
    set_smooth(obj)
    solidify = obj.modifiers.new(name="Shell Thickness", type="SOLIDIFY")
    solidify.thickness = thickness
    solidify.offset = -1.0
    add_bevel(obj, 0.0012, 2)
    return obj


def create_ellipsoid_patch(
    name: str,
    theta_range: tuple[float, float],
    phi_range: tuple[float, float],
    radii: tuple[float, float, float],
    center_z: float,
    material: bpy.types.Material,
    collection: bpy.types.Collection,
    thickness: float,
    bevel: float,
    theta_segments: int = 8,
    phi_segments: int = 12,
) -> bpy.types.Object:
    rx, ry, rz = radii
    vertices = []
    for row in range(theta_segments + 1):
        theta = theta_range[0] + (theta_range[1] - theta_range[0]) * row / theta_segments
        for column in range(phi_segments + 1):
            phi = phi_range[0] + (phi_range[1] - phi_range[0]) * column / phi_segments
            vertices.append(
                (
                    rx * math.sin(theta) * math.cos(phi),
                    ry * math.sin(theta) * math.sin(phi),
                    center_z + rz * math.cos(theta),
                )
            )
    stride = phi_segments + 1
    faces = []
    for row in range(theta_segments):
        for column in range(phi_segments):
            a = row * stride + column
            faces.append((a, a + stride, a + stride + 1, a + 1))
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    obj["aegis_managed"] = True
    assign_material(obj, material)
    set_smooth(obj)
    solidify = obj.modifiers.new(name="Panel Thickness", type="SOLIDIFY")
    solidify.thickness = thickness
    solidify.offset = -1.0
    add_bevel(obj, bevel, 3)
    return obj


def create_faceplate(
    material: bpy.types.Material, collection: bpy.types.Collection
) -> bpy.types.Object:
    rows = (
        (0.095, 0.077, -0.106),
        (0.068, 0.091, -0.119),
        (0.034, 0.086, -0.131),
        (0.002, 0.061, -0.134),
        (-0.036, 0.049, -0.129),
        (-0.075, 0.034, -0.121),
    )
    columns = 7
    vertices = []
    for z, half_width, base_y in rows:
        for column in range(columns):
            normalized_x = -1.0 + 2.0 * column / (columns - 1)
            x = normalized_x * half_width
            curvature = 1.0 - abs(normalized_x) ** 1.7
            y = base_y - 0.005 * curvature
            if z < 0.01:
                y -= 0.006 * (1.0 - abs(normalized_x))
            vertices.append((x, y, z))
    faces = []
    for row in range(len(rows) - 1):
        for column in range(columns - 1):
            a = row * columns + column
            faces.append((a, a + columns, a + columns + 1, a + 1))
    mesh = bpy.data.meshes.new("Faceplate_Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new("Faceplate", mesh)
    collection.objects.link(obj)
    obj["aegis_managed"] = True
    assign_material(obj, material)
    set_smooth(obj)
    solidify = obj.modifiers.new(name="Faceplate Thickness", type="SOLIDIFY")
    solidify.thickness = cfg.HELMET["panel_thickness"]
    solidify.offset = -1.0
    add_bevel(obj, cfg.HELMET["panel_bevel"], 3)
    return obj


def create_torus_segment(
    name: str,
    angle_start: float,
    angle_end: float,
    material: bpy.types.Material,
    collection: bpy.types.Collection,
) -> bpy.types.Object:
    major_radius = cfg.HELMET["neck_ring_radius"]
    minor_radius = cfg.HELMET["neck_ring_tube"]
    center_z = cfg.HELMET["neck_ring_z"]
    major_segments = 24
    minor_segments = 8
    vertices = []
    for major_index in range(major_segments + 1):
        angle = angle_start + (angle_end - angle_start) * major_index / major_segments
        for minor_index in range(minor_segments):
            tube_angle = 2.0 * math.pi * minor_index / minor_segments
            radial = major_radius + minor_radius * math.cos(tube_angle)
            vertices.append(
                (
                    radial * math.cos(angle),
                    radial * math.sin(angle),
                    center_z + minor_radius * math.sin(tube_angle),
                )
            )
    faces = []
    for major_index in range(major_segments):
        start = major_index * minor_segments
        next_start = start + minor_segments
        for minor_index in range(minor_segments):
            next_minor = (minor_index + 1) % minor_segments
            faces.append(
                (
                    start + minor_index,
                    next_start + minor_index,
                    next_start + next_minor,
                    start + next_minor,
                )
            )
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    obj["aegis_managed"] = True
    assign_material(obj, material)
    set_smooth(obj)
    add_bevel(obj, 0.0010, 2)
    return obj


def look_at(obj: bpy.types.Object, target: tuple[float, float, float]) -> None:
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def add_camera(
    name: str,
    location: tuple[float, float, float],
    camera_type: str,
    collection: bpy.types.Collection,
) -> bpy.types.Object:
    data = bpy.data.cameras.new(name)
    camera = bpy.data.objects.new(name, data)
    collection.objects.link(camera)
    camera["aegis_managed"] = True
    camera.location = location
    data.type = camera_type
    if camera_type == "ORTHO":
        data.ortho_scale = cfg.RENDER["ortho_scale"]
    else:
        data.lens = cfg.RENDER["perspective_lens"]
    data.dof.use_dof = False
    look_at(camera, cfg.RENDER["target"])
    return camera


def add_area_light(
    name: str,
    location: tuple[float, float, float],
    energy: float,
    size: float,
    collection: bpy.types.Collection,
) -> bpy.types.Object:
    data = bpy.data.lights.new(name=name, type="AREA")
    data.energy = energy
    data.shape = "DISK"
    data.size = size
    light = bpy.data.objects.new(name, data)
    collection.objects.link(light)
    light["aegis_managed"] = True
    light.location = location
    look_at(light, cfg.RENDER["target"])
    return light


def build_head_proxy(
    collection: bpy.types.Collection, material: bpy.types.Material
) -> list[bpy.types.Object]:
    head = cfg.HEAD
    objects = [
        add_uv_ellipsoid("HeadProxy_Cranium", head["cranium_center"], head["cranium_radii"], material, collection),
        add_uv_ellipsoid("HeadProxy_Face", head["face_center"], head["face_radii"], material, collection, 24, 16),
        add_uv_ellipsoid("HeadProxy_Jaw", (0.0, -0.014, -0.072), (0.059, 0.071, 0.042), material, collection, 24, 14),
        add_uv_ellipsoid("HeadProxy_Chin", (0.0, -0.054, -0.092), (0.036, 0.036, 0.021), material, collection, 20, 12),
    ]
    for side, sign in (("L", -1.0), ("R", 1.0)):
        objects.append(
            add_uv_ellipsoid(
                f"HeadProxy_Ear_{side}",
                (sign * 0.082, head["ear_center_y"], head["ear_center_z"]),
                head["ear_radii"],
                material,
                collection,
                18,
                12,
            )
        )
    objects.append(
        add_cylinder(
            "HeadProxy_Neck",
            (0.0, 0.010, head["neck_center_z"]),
            head["neck_diameter"] * 0.5,
            head["neck_depth"],
            material,
            collection,
            axis="Z",
            vertices=28,
            bevel=0.004,
        )
    )
    return objects


def build_helmet(
    collection: bpy.types.Collection,
    materials: dict[str, bpy.types.Material],
) -> tuple[bpy.types.Object, list[bpy.types.Object]]:
    root = bpy.data.objects.new("HelmetRoot", None)
    collection.objects.link(root)
    root["aegis_managed"] = True
    root.empty_display_type = "PLAIN_AXES"
    root.empty_display_size = 0.035

    helmet = cfg.HELMET
    outer = helmet["outer_radii"]
    center_z = helmet["center_z"]
    armor = materials["ArmorGray"]
    parts: list[bpy.types.Object] = []

    parts.append(
        create_open_ellipsoid_shell(
            "InnerShell",
            helmet["inner_radii"],
            center_z,
            helmet["shell_open_theta"],
            materials["InnerShell"],
            collection,
            helmet["shell_thickness"],
        )
    )

    patch_specs = (
        ("CrownFront", (0.12, 0.88), (-2.80, -0.34), outer),
        ("CrownRear", (0.12, 0.94), (0.34, 2.80), outer),
        ("RearShell_R", (0.82, 2.25), (0.18, 1.47), outer),
        ("RearShell_L", (0.82, 2.25), (1.67, 2.96), outer),
        ("Temple_R", (0.76, 1.36), (-1.48, -0.10), outer),
        ("Temple_L", (0.76, 1.36), (-3.04, -1.66), outer),
        ("Cheek_R", (1.40, 2.18), (-1.48, -0.18), (0.113, 0.133, 0.141)),
        ("Cheek_L", (1.40, 2.18), (-2.96, -1.66), (0.113, 0.133, 0.141)),
    )
    for name, theta_range, phi_range, radii in patch_specs:
        parts.append(
            create_ellipsoid_patch(
                name,
                theta_range,
                phi_range,
                radii,
                center_z,
                armor,
                collection,
                helmet["panel_thickness"],
                helmet["panel_bevel"],
            )
        )

    parts.append(create_faceplate(materials["FaceplateGray"], collection))

    jaw_right = [(0.029, -0.072), (0.076, -0.055), (0.103, -0.083), (0.073, -0.126), (0.029, -0.132)]
    jaw_left = [(-x, z) for x, z in reversed(jaw_right)]
    parts.append(create_prism("Jaw_R", jaw_right, -0.127, -0.105, armor, collection, 0.0028))
    parts.append(create_prism("Jaw_L", jaw_left, -0.127, -0.105, armor, collection, 0.0028))
    chin_polygon = [(-0.035, -0.073), (0.035, -0.073), (0.045, -0.105), (0.026, -0.138), (-0.026, -0.138), (-0.045, -0.105)]
    parts.append(create_prism("Chin", chin_polygon, -0.135, -0.105, armor, collection, 0.0030))

    angle = math.radians(helmet["eye_angle_degrees"])
    for side, sign in (("L", -1.0), ("R", 1.0)):
        rotation_y = angle if side == "L" else -angle
        parts.append(
            add_beveled_box(
                f"EyeHousing_{side}",
                (sign * helmet["eye_center_x"], -0.141, helmet["eye_center_z"]),
                (helmet["eye_width"], 0.012, helmet["eye_height"]),
                materials["EyeHousingDark"],
                collection,
                rotation=(0.0, rotation_y, 0.0),
                bevel=0.0025,
            )
        )
        parts.append(
            add_beveled_box(
                f"EyeLens_{side}",
                (sign * helmet["eye_center_x"], -0.148, helmet["eye_center_z"]),
                (0.044, 0.0035, 0.008),
                materials["EyeLensBlueGray"],
                collection,
                rotation=(0.0, rotation_y, 0.0),
                bevel=0.0018,
            )
        )

    for side, sign in (("L", -1.0), ("R", 1.0)):
        parts.append(
            add_cylinder(
                f"EarCover_{side}",
                (sign * helmet["ear_center_x"], helmet["ear_center_y"], helmet["ear_center_z"]),
                helmet["ear_radius"],
                helmet["ear_depth"],
                armor,
                collection,
                axis="X",
                vertices=36,
                bevel=0.0020,
            )
        )
        parts.append(
            add_cylinder(
                f"EarCore_{side}",
                (sign * (helmet["ear_center_x"] + 0.009), helmet["ear_center_y"], helmet["ear_center_z"]),
                0.023,
                0.008,
                materials["EyeHousingDark"],
                collection,
                axis="X",
                vertices=32,
                bevel=0.0012,
            )
        )

    parts.append(create_torus_segment("NeckRingFront", math.pi, 2.0 * math.pi, materials["NeckRingDark"], collection))
    parts.append(create_torus_segment("NeckRingRear", 0.0, math.pi, materials["NeckRingDark"], collection))

    for part in parts:
        parent_to_root(part, root)
    return root, parts


def build_render_rig(collection: bpy.types.Collection) -> None:
    for camera_spec in cfg.CAMERAS.values():
        add_camera(
            camera_spec["object"],
            camera_spec["location"],
            camera_spec["type"],
            collection,
        )
    add_area_light("Key_Light", (0.44, -0.52, 0.52), 90.0, 0.42, collection)
    add_area_light("Fill_Light", (-0.48, -0.28, 0.20), 48.0, 0.48, collection)
    add_area_light("Rim_Light", (0.34, 0.52, 0.42), 72.0, 0.38, collection)
    add_area_light("Rear_Fill_Light", (-0.38, 0.48, 0.02), 42.0, 0.42, collection)


def validate_scene(root: bpy.types.Object) -> None:
    missing = [name for name in cfg.REQUIRED_PARTS if bpy.data.objects.get(name) is None]
    if missing:
        raise RuntimeError(f"Missing required helmet objects: {missing}")
    duplicate_suffixes = [
        obj.name for obj in bpy.data.collections["HELMET"].all_objects if ".00" in obj.name
    ]
    if duplicate_suffixes:
        raise RuntimeError(f"Unexpected duplicate object suffixes: {duplicate_suffixes}")
    if root.location.length > 1.0e-8 or any(
        abs(value) > 1.0e-8 for value in root.rotation_euler
    ):
        raise RuntimeError("HelmetRoot transform is not identity")
    if any(abs(value - 1.0) > 1.0e-8 for value in root.scale):
        raise RuntimeError("HelmetRoot scale is not one")
    for obj in bpy.data.collections["HELMET"].all_objects:
        if any(value < 0.0 for value in obj.scale):
            raise RuntimeError(f"Negative scale on {obj.name}")


def main() -> None:
    started = time.perf_counter()
    ensure_directories()
    for name, path in cfg.REFERENCE_PATHS.items():
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(f"Required reference is missing or empty: {path}")

    remove_managed_collections()
    remove_factory_defaults_if_untouched()

    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0
    scene.render.film_transparent = False

    collections = {name: make_collection(name) for name in cfg.MANAGED_COLLECTIONS}
    materials = {name: make_material(name, spec) for name, spec in cfg.MATERIALS.items()}

    build_head_proxy(collections["HEAD_PROXY"], materials["HeadProxyGray"])
    root, parts = build_helmet(collections["HELMET"], materials)
    build_render_rig(collections["RENDER"])
    validate_scene(root)

    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=str(cfg.BLEND_PATH))

    elapsed = time.perf_counter() - started
    metrics = {
        "script": str(Path(__file__).resolve()),
        "elapsed_seconds": round(elapsed, 4),
        "helmet_part_count": len(parts),
        "blend_path": str(cfg.BLEND_PATH),
    }
    (cfg.LOG_DIR / "build_metrics.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"AEGIS_BUILD_OK parts={len(parts)} elapsed={elapsed:.3f}s")
    print(f"AEGIS_BLEND={cfg.BLEND_PATH}")


if __name__ == "__main__":
    main()
