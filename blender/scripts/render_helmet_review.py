"""Render the AEGIS-R7 graybox from all required review cameras."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import bpy


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import helmet_config as cfg


def choose_eevee_engine(scene: bpy.types.Scene) -> str:
    engine_property = bpy.types.RenderSettings.bl_rna.properties["engine"]
    supported = {item.identifier for item in engine_property.enum_items}
    for candidate in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        if candidate in supported:
            scene.render.engine = candidate
            return candidate
    raise RuntimeError(f"Eevee is unavailable. Available engines: {sorted(supported)}")


def ensure_world(scene: bpy.types.Scene) -> None:
    world = scene.world or bpy.data.worlds.new("AEGIS Review World")
    scene.world = world
    world.use_nodes = True
    nodes = world.node_tree.nodes
    nodes.clear()
    background = nodes.new(type="ShaderNodeBackground")
    output = nodes.new(type="ShaderNodeOutputWorld")
    world.node_tree.links.new(background.outputs["Background"], output.inputs["Surface"])
    background.inputs["Color"].default_value = cfg.RENDER["world_color"]
    background.inputs["Strength"].default_value = cfg.RENDER["world_strength"]


def configure_render(scene: bpy.types.Scene) -> str:
    engine = choose_eevee_engine(scene)
    ensure_world(scene)
    resolution = cfg.RENDER["resolution"]
    scene.render.resolution_x = resolution
    scene.render.resolution_y = resolution
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.color_depth = "8"
    scene.render.film_transparent = False
    if hasattr(scene.render, "use_motion_blur"):
        scene.render.use_motion_blur = False
    scene.view_settings.exposure = 0.0
    return engine


def validate_scene() -> None:
    if bpy.data.objects.get("HelmetRoot") is None:
        raise RuntimeError("HelmetRoot is missing; run build_helmet_graybox.py first")
    for camera_spec in cfg.CAMERAS.values():
        camera = bpy.data.objects.get(camera_spec["object"])
        if camera is None or camera.type != "CAMERA":
            raise RuntimeError(f"Review camera is missing: {camera_spec['object']}")


def set_material_alpha(material_name: str, alpha: float) -> None:
    material = bpy.data.materials.get(material_name)
    if material is None or material.node_tree is None:
        return
    principled = material.node_tree.nodes.get("Principled BSDF")
    if principled and "Alpha" in principled.inputs:
        principled.inputs["Alpha"].default_value = alpha
    diffuse = list(material.diffuse_color)
    diffuse[3] = alpha
    material.diffuse_color = diffuse
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "DITHERED"
    elif hasattr(material, "blend_method"):
        material.blend_method = "HASHED"


def configure_fit_review_transparency() -> None:
    """Reveal the proxy for fit review without saving changes to the Blend file."""
    for name, alpha in {
        "InnerShell": 0.16,
        "ArmorGray": 0.30,
        "FaceplateGray": 0.24,
        "EyeHousingDark": 0.55,
        "EyeLensBlueGray": 0.72,
        "NeckRingDark": 0.34,
        "HeadProxyGray": 0.70,
    }.items():
        set_material_alpha(name, alpha)


def render_set(
    scene: bpy.types.Scene,
    destination: Path,
    head_proxy_collection: bpy.types.Collection,
    include_head_proxy: bool,
) -> list[str]:
    destination.mkdir(parents=True, exist_ok=True)
    head_proxy_collection.hide_render = not include_head_proxy
    if include_head_proxy:
        configure_fit_review_transparency()
    rendered = []
    for output_name in cfg.REVIEW_NAMES:
        camera_name = cfg.CAMERAS[output_name]["object"]
        scene.camera = bpy.data.objects[camera_name]
        output_path = destination / f"{output_name}.png"
        scene.render.filepath = str(output_path)
        bpy.ops.render.render(write_still=True)
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise RuntimeError(f"Render was not written: {output_path}")
        rendered.append(str(output_path))
        print(f"AEGIS_RENDERED={output_path}")
    return rendered


def main() -> None:
    started = time.perf_counter()
    if bpy.data.objects.get("HelmetRoot") is None:
        if not cfg.BLEND_PATH.is_file():
            raise FileNotFoundError(f"Graybox Blend is missing: {cfg.BLEND_PATH}")
        bpy.ops.wm.open_mainfile(filepath=str(cfg.BLEND_PATH))

    validate_scene()
    scene = bpy.context.scene
    engine = configure_render(scene)
    proxy_collection = bpy.data.collections.get("HEAD_PROXY")
    if proxy_collection is None:
        raise RuntimeError("HEAD_PROXY collection is missing")

    rendered = []
    rendered.extend(render_set(scene, cfg.HELMET_ONLY_DIR, proxy_collection, False))
    rendered.extend(render_set(scene, cfg.WITH_HEAD_DIR, proxy_collection, True))
    proxy_collection.hide_render = False

    elapsed = time.perf_counter() - started
    metrics = {
        "script": str(Path(__file__).resolve()),
        "elapsed_seconds": round(elapsed, 4),
        "engine": engine,
        "resolution": [cfg.RENDER["resolution"], cfg.RENDER["resolution"]],
        "render_count": len(rendered),
        "outputs": rendered,
    }
    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    (cfg.LOG_DIR / "render_metrics.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"AEGIS_RENDER_OK count={len(rendered)} engine={engine} elapsed={elapsed:.3f}s")


if __name__ == "__main__":
    main()
