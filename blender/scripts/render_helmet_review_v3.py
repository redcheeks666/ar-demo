"""Render all AEGIS-R7 v3 modeling and visual-review passes."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import bpy
from mathutils import Vector


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import helmet_v3_config as cfg


def parse_args() -> argparse.Namespace:
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=("master-silhouette", "all"),
        default="all",
    )
    parser.add_argument("--iteration", type=int, default=1)
    return parser.parse_args(arguments)


def choose_eevee(scene: bpy.types.Scene) -> str:
    engine_property = bpy.types.RenderSettings.bl_rna.properties["engine"]
    supported = {item.identifier for item in engine_property.enum_items}
    for candidate in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        if candidate in supported:
            scene.render.engine = candidate
            return candidate
    raise RuntimeError(f"Eevee unavailable: {sorted(supported)}")


def configure_world(scene: bpy.types.Scene, transparent: bool) -> None:
    world = scene.world or bpy.data.worlds.new("AEGIS V3 Review World")
    scene.world = world
    world.use_nodes = True
    nodes = world.node_tree.nodes
    nodes.clear()
    background = nodes.new(type="ShaderNodeBackground")
    output = nodes.new(type="ShaderNodeOutputWorld")
    world.node_tree.links.new(background.outputs["Background"], output.inputs["Surface"])
    background.inputs["Color"].default_value = cfg.RENDER["world_color"]
    background.inputs["Strength"].default_value = cfg.RENDER["world_strength"]
    scene.render.film_transparent = transparent


def configure_render(scene: bpy.types.Scene, resolution: int, transparent: bool) -> None:
    choose_eevee(scene)
    configure_world(scene, transparent)
    scene.render.resolution_x = resolution
    scene.render.resolution_y = resolution
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA" if transparent else "RGB"
    scene.render.image_settings.color_depth = "8"
    if hasattr(scene.render, "use_motion_blur"):
        scene.render.use_motion_blur = False
    scene.view_settings.exposure = 0.0


def collection_objects(name: str) -> list[bpy.types.Object]:
    collection = bpy.data.collections.get(name)
    return list(collection.all_objects) if collection else []


def reset_visibility() -> None:
    for obj in bpy.data.objects:
        obj.hide_render = obj.type in {"CAMERA", "LIGHT"} and False
    for obj in collection_objects("REFERENCE_V3"):
        obj.hide_render = True


def set_group_visibility(
    helmet: bool,
    master: bool,
    proxy: bool,
) -> None:
    for obj in collection_objects("HELMET_V3"):
        obj.hide_render = not helmet
    master_obj = bpy.data.objects.get("HelmetEnvelope_Master")
    if master_obj is not None:
        master_obj.hide_render = not master
    for obj in collection_objects("HEAD_PROXY_V3"):
        obj.hide_render = not proxy


def assign_review_material(mode: str) -> None:
    for obj in collection_objects("HELMET_V3"):
        if obj.type != "MESH":
            continue
        if mode == "silhouette":
            material_name = "Silhouette"
        else:
            property_name = "clay_material" if mode == "clay" else "design_material"
            material_name = obj.get(property_name)
            if not material_name:
                continue
        material = bpy.data.materials.get(material_name)
        if material is None:
            raise RuntimeError(f"Missing review material {material_name} for {obj.name}")
        obj.data.materials.clear()
        obj.data.materials.append(material)


def set_master_silhouette_material() -> None:
    master = bpy.data.objects.get("HelmetEnvelope_Master")
    material = bpy.data.materials.get("Silhouette")
    if master is None or material is None:
        raise RuntimeError("Master envelope or silhouette material is missing")
    master.data.materials.clear()
    master.data.materials.append(material)


def render_views(
    scene: bpy.types.Scene,
    output_dir: Path,
    views: tuple[str, ...] | list[str],
) -> list[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    for view in views:
        camera_name = cfg.CAMERAS[view][0]
        camera = bpy.data.objects.get(camera_name)
        if camera is None:
            raise RuntimeError(f"Missing v3 review camera: {camera_name}")
        scene.camera = camera
        path = output_dir / f"{view}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"Render failed: {path}")
        outputs.append(str(path))
        print(f"AEGIS_V3_RENDERED={path}")
    return outputs


def render_master_silhouette(scene: bpy.types.Scene, iteration: int) -> list[str]:
    configure_render(scene, cfg.RENDER["silhouette_resolution"], True)
    set_group_visibility(helmet=False, master=True, proxy=False)
    set_master_silhouette_material()
    views = ("front", "right", "left", "top", "front_3q")
    return render_views(
        scene,
        cfg.OUTPUT_DIR / "silhouette_iterations" / f"iteration_{iteration}",
        views,
    )


def render_clay(scene: bpy.types.Scene) -> list[str]:
    configure_render(scene, cfg.RENDER["resolution"], False)
    set_group_visibility(helmet=True, master=False, proxy=False)
    assign_review_material("clay")
    return render_views(scene, cfg.OUTPUT_DIR / "clay" / "helmet_only", list(cfg.REVIEW_VIEWS))


def render_design(scene: bpy.types.Scene) -> list[str]:
    configure_render(scene, cfg.RENDER["resolution"], False)
    set_group_visibility(helmet=True, master=False, proxy=False)
    assign_review_material("design")
    return render_views(scene, cfg.OUTPUT_DIR / "design_preview" / "helmet_only", list(cfg.REVIEW_VIEWS))


def render_proxy_cutaway(scene: bpy.types.Scene) -> list[str]:
    configure_render(scene, cfg.RENDER["resolution"], False)
    set_group_visibility(helmet=True, master=False, proxy=True)
    assign_review_material("clay")
    hidden_names = {
        "CrownFront",
        "Temple_L",
        "Cheek_L",
        "Jaw_L",
        "RearShell_L",
        "EarCover_L",
        "InnerShell",
    }
    for name in hidden_names:
        obj = bpy.data.objects.get(name)
        if obj is not None:
            obj.hide_render = True
    outputs = render_views(scene, cfg.OUTPUT_DIR / "with_head_proxy", list(cfg.REVIEW_VIEWS))
    for name in hidden_names:
        obj = bpy.data.objects.get(name)
        if obj is not None:
            obj.hide_render = False
    for obj in collection_objects("HEAD_PROXY_V3"):
        obj.hide_render = True
    return outputs


def render_final_silhouette(scene: bpy.types.Scene) -> list[str]:
    configure_render(scene, cfg.RENDER["silhouette_resolution"], True)
    set_group_visibility(helmet=True, master=False, proxy=False)
    assign_review_material("silhouette")
    return render_views(scene, cfg.OUTPUT_DIR / "silhouette", ["front", "left", "right", "top"])


def render_wireframe(scene: bpy.types.Scene) -> list[str]:
    configure_render(scene, cfg.RENDER["resolution"], False)
    set_group_visibility(helmet=True, master=False, proxy=False)
    assign_review_material("clay")
    modifiers: list[tuple[bpy.types.Object, bpy.types.Modifier]] = []
    for obj in collection_objects("HELMET_V3"):
        if obj.type != "MESH":
            continue
        modifier = obj.modifiers.new(name="TEMP_V3_WIREFRAME", type="WIREFRAME")
        modifier.thickness = 0.00032
        modifier.use_replace = True
        modifiers.append((obj, modifier))
    try:
        return render_views(scene, cfg.OUTPUT_DIR / "wireframe", ["front", "right", "front_3q"])
    finally:
        for obj, modifier in modifiers:
            if modifier.name in obj.modifiers:
                obj.modifiers.remove(modifier)


def render_exploded(scene: bpy.types.Scene) -> list[str]:
    configure_render(scene, cfg.RENDER["resolution"], False)
    set_group_visibility(helmet=True, master=False, proxy=False)
    assign_review_material("design")
    originals = {}
    for obj in collection_objects("HELMET_V3"):
        if obj.type != "MESH":
            continue
        originals[obj.name] = obj.location.copy()
        direction = obj.location.copy()
        direction.z *= 0.45
        if direction.length < 0.01:
            direction = Vector((0.0, -1.0, 0.0))
        obj.location += direction.normalized() * 0.032
    try:
        return render_views(scene, cfg.OUTPUT_DIR / "exploded_sanity", ["front_3q"])
    finally:
        for name, location in originals.items():
            bpy.data.objects[name].location = location


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    scene = bpy.context.scene
    if bpy.data.objects.get("HelmetEnvelope_Master") is None:
        if not cfg.BLEND_PATH.is_file():
            raise FileNotFoundError(f"Missing v3 Blend: {cfg.BLEND_PATH}")
        bpy.ops.wm.open_mainfile(filepath=str(cfg.BLEND_PATH))
        scene = bpy.context.scene
    reset_visibility()
    outputs = []
    if args.mode == "master-silhouette":
        outputs.extend(render_master_silhouette(scene, args.iteration))
    else:
        outputs.extend(render_clay(scene))
        outputs.extend(render_design(scene))
        outputs.extend(render_proxy_cutaway(scene))
        outputs.extend(render_wireframe(scene))
        outputs.extend(render_final_silhouette(scene))
        outputs.extend(render_exploded(scene))
    elapsed = time.perf_counter() - started
    metrics = {
        "script": str(Path(__file__).resolve()),
        "mode": args.mode,
        "iteration": args.iteration,
        "engine": scene.render.engine,
        "elapsed_seconds": round(elapsed, 4),
        "render_count": len(outputs),
        "outputs": outputs,
    }
    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_name = f"iteration_{args.iteration}_silhouette_render.json" if args.mode == "master-silhouette" else "final_render.json"
    (cfg.LOG_DIR / log_name).write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"AEGIS_V3_RENDER_OK mode={args.mode} count={len(outputs)} elapsed={elapsed:.3f}s")


if __name__ == "__main__":
    main()
