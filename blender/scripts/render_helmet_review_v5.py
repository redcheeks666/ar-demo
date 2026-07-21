"""Headless visual-review renderer for the AEGIS-R7 graybox v5 craft pass.

Besides the normal clay/design review passes, this renderer creates a real
zebra-reflection pass.  The faceplate is temporarily assigned a low-roughness
metallic material and reflects both a procedural striped world and a bank of
narrow rectangular area lights.  The stripes are therefore specular response
to surface normals, not colors painted onto the faceplate.
"""

from __future__ import annotations

import argparse
import hashlib
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

import helmet_v5_config as cfg
import render_helmet_review_v3 as v3render


v3render.cfg = cfg

ITERATION_VIEWS = ("front", "front_3q", "left", "right", "back", "top")
FINAL_VIEWS = ("front", "back", "left", "right", "front_3q", "rear_3q", "top", "bottom")
ZEBRA_VIEWS = ("front", "front_3q", "left")
SILHOUETTE_VIEWS = ("front", "left", "right", "back", "top")
MATERIAL_ID_VIEWS = ("left", "right")

# Filled immediately after opening the blend.  Boolean cutters and other
# helpers that the builder deliberately left hidden must never be revealed by
# a review isolation pass.
BASE_HIDDEN_NAMES: set[str] = set()


def parse_args() -> argparse.Namespace:
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("iteration", "all"), default="all")
    parser.add_argument("--iteration", type=int, default=1)
    parsed = parser.parse_args(arguments)
    if parsed.iteration < 1:
        parser.error("--iteration must be >= 1")
    return parsed


def collection_objects(name: str) -> list[bpy.types.Object]:
    collection = bpy.data.collections.get(name)
    return list(collection.all_objects) if collection else []


def helmet_objects() -> list[bpy.types.Object]:
    # v4 kept the protected v3 collection name inside its isolated blend.  The
    # union makes the renderer tolerant if v5 puts only its new craft pieces in
    # a clearer collection while retaining protected base pieces in HELMET_V3.
    found: dict[str, bpy.types.Object] = {}
    for name in ("HELMET_V5", "HELMET_V4", "HELMET_V3"):
        for obj in collection_objects(name):
            found[obj.name] = obj
    if found:
        return list(found.values())
    raise RuntimeError("Missing v5 helmet collection (HELMET_V5/HELMET_V4/HELMET_V3)")


def proxy_objects() -> list[bpy.types.Object]:
    found: dict[str, bpy.types.Object] = {}
    for name in ("HEAD_PROXY_V5", "HEAD_PROXY_V4", "HEAD_PROXY_V3"):
        for obj in collection_objects(name):
            found[obj.name] = obj
    return list(found.values())


def renderable_helmet_objects() -> list[bpy.types.Object]:
    return [obj for obj in helmet_objects() if obj.name not in BASE_HIDDEN_NAMES]


def isolate(helmet: bool, master: bool, proxy: bool) -> None:
    for obj in bpy.data.objects:
        if obj.type not in {"CAMERA", "LIGHT"}:
            obj.hide_render = True
    if helmet:
        for obj in renderable_helmet_objects():
            obj.hide_render = False
    master_obj = bpy.data.objects.get("HelmetEnvelope_Master")
    if master_obj is not None:
        master_obj.hide_render = not master
    if proxy:
        for obj in proxy_objects():
            if obj.name not in BASE_HIDDEN_NAMES:
                obj.hide_render = False
    for name in ("REFERENCE_V5", "REFERENCE_V4", "REFERENCE_V3"):
        for obj in collection_objects(name):
            obj.hide_render = True
    for obj in bpy.data.objects:
        if obj.type in {"CAMERA", "LIGHT"}:
            obj.hide_render = False


def material_for_object(obj: bpy.types.Object, mode: str) -> bpy.types.Material:
    if mode == "silhouette":
        name = "Silhouette"
    else:
        property_name = "clay_material" if mode == "clay" else "design_material"
        name = obj.get(property_name)
    material = bpy.data.materials.get(name) if name else None
    if material is None:
        raise RuntimeError(f"Missing {mode} material assignment on {obj.name}: {name}")
    return material


def assign_material(mode: str) -> None:
    for obj in renderable_helmet_objects():
        if obj.type != "MESH":
            continue
        material = material_for_object(obj, mode)
        obj.data.materials.clear()
        obj.data.materials.append(material)


def make_principled_material(
    name: str,
    base_color: tuple[float, float, float, float],
    metallic: float,
    roughness: float,
) -> bpy.types.Material:
    material = bpy.data.materials.get(name) or bpy.data.materials.new(name=name)
    material.use_nodes = True
    material.use_fake_user = True
    material.diffuse_color = base_color
    nodes = material.node_tree.nodes
    nodes.clear()
    shader = nodes.new(type="ShaderNodeBsdfPrincipled")
    shader.name = "AEGIS V5 Review Principled"
    shader.inputs["Base Color"].default_value = base_color
    shader.inputs["Metallic"].default_value = metallic
    shader.inputs["Roughness"].default_value = roughness
    coat = shader.inputs.get("Coat Weight") or shader.inputs.get("Clearcoat")
    if coat is not None:
        coat.default_value = 0.24 if metallic > 0.5 else 0.0
    output = nodes.new(type="ShaderNodeOutputMaterial")
    material.node_tree.links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    return material


def make_id_material(name: str, color: tuple[float, float, float, float]) -> bpy.types.Material:
    material = bpy.data.materials.get(name) or bpy.data.materials.new(name=name)
    material.use_nodes = True
    material.use_fake_user = True
    material.diffuse_color = color
    nodes = material.node_tree.nodes
    nodes.clear()
    emission = nodes.new(type="ShaderNodeEmission")
    emission.inputs["Color"].default_value = color
    emission.inputs["Strength"].default_value = 1.0
    output = nodes.new(type="ShaderNodeOutputMaterial")
    material.node_tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return material


def is_gold_faceplate(obj: bpy.types.Object) -> bool:
    design_name = str(obj.get("design_material") or "")
    return obj.name == "Faceplate" or (
        obj.get("feature_owner") == "Faceplate" and "Gold" in design_name
    )


def assign_zebra_materials() -> None:
    neutral = make_principled_material("AEGIS_V5_ZebraNeutral", (0.025, 0.030, 0.038, 1.0), 0.0, 0.72)
    reflective = make_principled_material("AEGIS_V5_ZebraReflective", (0.34, 0.36, 0.39, 1.0), 1.0, 0.075)
    reflective_count = 0
    for obj in renderable_helmet_objects():
        if obj.type != "MESH":
            continue
        obj.data.materials.clear()
        if is_gold_faceplate(obj):
            obj.data.materials.append(reflective)
            reflective_count += 1
        else:
            obj.data.materials.append(neutral)
    if reflective_count == 0:
        raise RuntimeError("Zebra pass found no Faceplate mesh to receive the reflective material")


def assign_material_ids() -> None:
    identifiers = {
        "gold": make_id_material("AEGIS_V5_ID_Gold", (1.0, 0.72, 0.0, 1.0)),
        "red": make_id_material("AEGIS_V5_ID_Red", (0.95, 0.015, 0.01, 1.0)),
        "black": make_id_material("AEGIS_V5_ID_Black", (0.01, 0.01, 0.012, 1.0)),
        "cyan": make_id_material("AEGIS_V5_ID_Cyan", (0.0, 0.92, 1.0, 1.0)),
    }
    for obj in renderable_helmet_objects():
        if obj.type != "MESH":
            continue
        design_name = str(obj.get("design_material") or "")
        lowered = f"{obj.name} {design_name}".lower()
        if is_gold_faceplate(obj):
            key = "gold"
        elif any(token in lowered for token in ("lens", "emitter", "emission", "cyan")):
            key = "cyan"
        elif any(token in lowered for token in ("black", "gunmetal", "dark", "vent", "trim", "housing", "inner")):
            key = "black"
        else:
            key = "red"
        obj.data.materials.clear()
        obj.data.materials.append(identifiers[key])


def configure_zebra_world(scene: bpy.types.Scene) -> None:
    world = scene.world or bpy.data.worlds.new("AEGIS V5 Zebra World")
    scene.world = world
    world.use_nodes = True
    nodes = world.node_tree.nodes
    nodes.clear()
    texture = nodes.new(type="ShaderNodeTexCoord")
    mapping = nodes.new(type="ShaderNodeMapping")
    mapping.vector_type = "POINT"
    mapping.inputs["Rotation"].default_value[1] = math.radians(-13.0)
    mapping.inputs["Rotation"].default_value[2] = math.radians(24.0)
    wave = nodes.new(type="ShaderNodeTexWave")
    wave.wave_type = "BANDS"
    wave.bands_direction = "X"
    # Four broad angular cycles remain legible at both 768 px review size and
    # downsampled contact-sheet size; the earlier eight-cycle probe produced
    # moire-like micro-stripes that obscured the actual normal flow.
    wave.inputs["Scale"].default_value = 4.0
    wave.inputs["Distortion"].default_value = 0.0
    ramp = nodes.new(type="ShaderNodeValToRGB")
    ramp.color_ramp.interpolation = "CONSTANT"
    ramp.color_ramp.elements[0].position = 0.48
    ramp.color_ramp.elements[0].color = (0.003, 0.003, 0.004, 1.0)
    ramp.color_ramp.elements[1].position = 0.52
    ramp.color_ramp.elements[1].color = (1.0, 1.0, 1.0, 1.0)
    background = nodes.new(type="ShaderNodeBackground")
    background.inputs["Strength"].default_value = 0.82
    output = nodes.new(type="ShaderNodeOutputWorld")
    links = world.node_tree.links
    links.new(texture.outputs["Normal"], mapping.inputs["Vector"])
    links.new(mapping.outputs["Vector"], wave.inputs["Vector"])
    links.new(wave.outputs["Fac"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], background.inputs["Color"])
    links.new(background.outputs["Background"], output.inputs["Surface"])
    # Transparent film keeps the stripe source out of the camera background,
    # while it remains available to glossy rays on the faceplate.
    scene.render.film_transparent = True
    scene.render.image_settings.color_mode = "RGBA"


def create_zebra_lights() -> tuple[list[bpy.types.Object], dict[str, bool]]:
    previous = {obj.name: obj.hide_render for obj in bpy.data.objects if obj.type == "LIGHT"}
    for obj in bpy.data.objects:
        if obj.type == "LIGHT":
            obj.hide_render = True
    collection = bpy.data.collections.get("AEGIS_V5_ZEBRA_HELPERS")
    if collection is None:
        collection = bpy.data.collections.new("AEGIS_V5_ZEBRA_HELPERS")
        bpy.context.scene.collection.children.link(collection)
    created: list[bpy.types.Object] = []

    def add_bank(prefix: str, locations: list[Vector], target: Vector) -> None:
        for index, location in enumerate(locations, 1):
            data = bpy.data.lights.new(name=f"{prefix}_{index:02d}_Data", type="AREA")
            data.energy = 4.0
            data.shape = "RECTANGLE"
            data.size = 0.010
            data.size_y = 0.34
            light = bpy.data.objects.new(name=f"{prefix}_{index:02d}", object_data=data)
            light.location = location
            direction = target - location
            light.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
            collection.objects.link(light)
            created.append(light)

    add_bank(
        "AEGIS_V5_ZebraFront",
        [Vector((x, -0.40, 0.025)) for x in (-0.150, -0.108, -0.066, -0.024, 0.018, 0.060, 0.102, 0.144)],
        Vector((0.0, -0.105, 0.010)),
    )
    add_bank(
        "AEGIS_V5_ZebraLeft",
        [Vector((-0.40, y, 0.025)) for y in (-0.155, -0.110, -0.065, -0.020, 0.025, 0.070, 0.115)],
        Vector((-0.105, -0.015, 0.010)),
    )
    return created, previous


def remove_zebra_lights(created: list[bpy.types.Object], previous: dict[str, bool]) -> None:
    for obj in created:
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data is not None and data.users == 0:
            bpy.data.lights.remove(data)
    collection = bpy.data.collections.get("AEGIS_V5_ZEBRA_HELPERS")
    if collection is not None and not collection.objects:
        bpy.data.collections.remove(collection)
    for name, hidden in previous.items():
        obj = bpy.data.objects.get(name)
        if obj is not None:
            obj.hide_render = hidden


def render_views(
    scene: bpy.types.Scene,
    output_dir: Path,
    views: tuple[str, ...] | list[str],
    manifest: list[dict],
    pass_name: str,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for view in views:
        if view not in cfg.CAMERAS:
            raise RuntimeError(f"v5 config has no camera mapping for {view}")
        camera_name = cfg.CAMERAS[view][0]
        camera = bpy.data.objects.get(camera_name)
        if camera is None:
            raise RuntimeError(f"Missing v5 review camera {camera_name}")
        scene.camera = camera
        path = output_dir / f"{view}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"Render failed: {path}")
        manifest.append(
            {
                "path": str(path),
                "pass": pass_name,
                "view": view,
                "camera": camera_name,
                "size_bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "resolution": [scene.render.resolution_x, scene.render.resolution_y],
                "camera_matrix_world": [round(value, 7) for row in camera.matrix_world for value in row],
            }
        )
        print(f"AEGIS_V5_RENDERED={path}")


def render_zebra(scene: bpy.types.Scene, output_dir: Path, manifest: list[dict], pass_name: str) -> None:
    v3render.configure_render(scene, cfg.RENDER["resolution"], True)
    isolate(helmet=True, master=False, proxy=False)
    assign_zebra_materials()
    configure_zebra_world(scene)
    created, previous = create_zebra_lights()
    try:
        render_views(scene, output_dir, ZEBRA_VIEWS, manifest, pass_name)
    finally:
        remove_zebra_lights(created, previous)


def render_iteration(scene: bpy.types.Scene, iteration: int, manifest: list[dict]) -> None:
    base_dir = cfg.OUTPUT_DIR / "review_iterations" / f"iteration_{iteration:02d}"
    v3render.configure_render(scene, cfg.RENDER["resolution"], False)
    isolate(helmet=True, master=False, proxy=False)
    assign_material("design")
    render_views(scene, base_dir / "design", ITERATION_VIEWS, manifest, "iteration_design")
    render_zebra(scene, base_dir / "zebra", manifest, "iteration_zebra")


def render_final(scene: bpy.types.Scene, manifest: list[dict]) -> None:
    v3render.configure_render(scene, cfg.RENDER["resolution"], False)
    isolate(helmet=True, master=False, proxy=False)
    assign_material("clay")
    render_views(scene, cfg.OUTPUT_DIR / "clay" / "helmet_only", FINAL_VIEWS, manifest, "clay")

    v3render.configure_render(scene, cfg.RENDER["resolution"], False)
    isolate(helmet=True, master=False, proxy=False)
    assign_material("design")
    render_views(scene, cfg.OUTPUT_DIR / "design_preview" / "helmet_only", FINAL_VIEWS, manifest, "design")

    v3render.configure_render(scene, cfg.RENDER["resolution"], False)
    isolate(helmet=True, master=False, proxy=True)
    assign_material("clay")
    hidden = {"CrownFront", "Temple_L", "Cheek_L", "Jaw_L", "RearShell_L", "EarCover_L", "InnerShell"}
    for obj in renderable_helmet_objects():
        if obj.name in hidden or obj.get("feature_owner") in hidden or obj.name.endswith("_L"):
            obj.hide_render = True
    render_views(scene, cfg.OUTPUT_DIR / "with_head_proxy", FINAL_VIEWS, manifest, "with_head_proxy")

    v3render.configure_render(scene, cfg.RENDER["resolution"], False)
    isolate(helmet=True, master=False, proxy=False)
    assign_material("design")
    meshes = [obj for obj in renderable_helmet_objects() if obj.type == "MESH"]
    originals = {obj.name: obj.location.copy() for obj in meshes}
    owner_displacements: dict[str, Vector] = {}
    for obj in meshes:
        if obj.get("feature_owner"):
            continue
        direction = obj.location.copy()
        direction.z *= 0.45
        if direction.length < 0.01:
            direction = Vector((0.0, -1.0, 0.0))
        owner_displacements[obj.name] = direction.normalized() * 0.032
    for obj in meshes:
        owner = obj.get("feature_owner") or obj.name
        obj.location += owner_displacements.get(owner, Vector((0.0, -0.032, 0.0)))
    try:
        render_views(scene, cfg.OUTPUT_DIR / "exploded_sanity", ("front_3q",), manifest, "exploded_sanity")
    finally:
        for name, location in originals.items():
            bpy.data.objects[name].location = location

    v3render.configure_render(scene, cfg.RENDER["silhouette_resolution"], True)
    isolate(helmet=True, master=False, proxy=False)
    assign_material("silhouette")
    render_views(scene, cfg.OUTPUT_DIR / "silhouette", SILHOUETTE_VIEWS, manifest, "silhouette")

    # Deterministic emission colors give silhouette_material_check_v5.py a
    # render-level source for checking that gold never owns the side outline.
    v3render.configure_render(scene, cfg.RENDER["silhouette_resolution"], True)
    isolate(helmet=True, master=False, proxy=False)
    assign_material_ids()
    try:
        scene.view_settings.view_transform = "Standard"
    except TypeError:
        pass
    render_views(
        scene,
        cfg.OUTPUT_DIR / "silhouette" / "material_id",
        MATERIAL_ID_VIEWS,
        manifest,
        "silhouette_material_id",
    )

    render_zebra(scene, cfg.OUTPUT_DIR / "zebra", manifest, "zebra")


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    if not cfg.BLEND_PATH.is_file():
        raise FileNotFoundError(f"Missing v5 blend: {cfg.BLEND_PATH}")
    bpy.ops.wm.open_mainfile(filepath=str(cfg.BLEND_PATH))
    scene = bpy.context.scene
    global BASE_HIDDEN_NAMES
    BASE_HIDDEN_NAMES = {
        obj.name
        for obj in helmet_objects() + proxy_objects()
        if obj.hide_render or obj.get("review_exclude") or obj.get("aegis_helper")
    }
    manifest: list[dict] = []
    if args.mode == "iteration":
        render_iteration(scene, args.iteration, manifest)
    else:
        render_final(scene, manifest)
    log = {
        "script": str(Path(__file__).resolve()),
        "mode": args.mode,
        "iteration": args.iteration,
        "engine": scene.render.engine,
        "elapsed_seconds": round(time.perf_counter() - started, 4),
        "render_count": len(manifest),
        "zebra_contract": {
            "views": list(ZEBRA_VIEWS),
            "faceplate_material": "AEGIS_V5_ZebraReflective",
            "material_metallic": 1.0,
            "material_roughness": 0.075,
            "procedural_world_bands": 4.0,
            "specular_area_light_banks": ["front", "left"],
            "stripe_source_is_reflection_not_base_color": True,
        },
        "material_id_contract": {
            "views": list(MATERIAL_ID_VIEWS),
            "gold_rgba": [1.0, 0.72, 0.0, 1.0],
            "output_dir": str(cfg.OUTPUT_DIR / "silhouette" / "material_id"),
        },
        "renders": manifest,
    }
    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    name = f"iteration_{args.iteration}_render.json" if args.mode == "iteration" else "final_render.json"
    (cfg.LOG_DIR / name).write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"AEGIS_V5_RENDER_OK mode={args.mode} count={len(manifest)}")


if __name__ == "__main__":
    main()
