"""Headless visual-review renderer for AEGIS-R7 graybox v4."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import bpy
from mathutils import Vector


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import helmet_v4_config as cfg
import render_helmet_review_v3 as v3render


v3render.cfg = cfg


def parse_args() -> argparse.Namespace:
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("iteration", "all", "silhouette"), default="all")
    parser.add_argument("--iteration", type=int, default=1)
    return parser.parse_args(arguments)


def collection_objects(name: str) -> list[bpy.types.Object]:
    collection = bpy.data.collections.get(name)
    return list(collection.all_objects) if collection else []


def isolate(helmet: bool, master: bool, proxy: bool) -> None:
    for obj in bpy.data.objects:
        if obj.type not in {"CAMERA", "LIGHT"}:
            obj.hide_render = True
    for obj in collection_objects("HELMET_V3"):
        obj.hide_render = not helmet
    master_obj = bpy.data.objects.get("HelmetEnvelope_Master")
    if master_obj is not None:
        master_obj.hide_render = not master
    for obj in collection_objects("HEAD_PROXY_V3"):
        obj.hide_render = not proxy
    for obj in collection_objects("REFERENCE_V3"):
        obj.hide_render = True
    for obj in bpy.data.objects:
        if obj.type in {"CAMERA", "LIGHT"}:
            obj.hide_render = False


def assign_material(mode: str) -> None:
    for obj in collection_objects("HELMET_V3"):
        if obj.type != "MESH":
            continue
        if mode == "silhouette":
            name = "Silhouette"
        else:
            name = obj.get("clay_material" if mode == "clay" else "design_material")
        if not name or bpy.data.materials.get(name) is None:
            raise RuntimeError(f"Missing {mode} material assignment on {obj.name}: {name}")
        obj.data.materials.clear()
        obj.data.materials.append(bpy.data.materials[name])


def render_views(scene: bpy.types.Scene, output_dir: Path, views: list[str] | tuple[str, ...], manifest: list[dict]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for view in views:
        camera_name = cfg.CAMERAS[view][0]
        camera = bpy.data.objects.get(camera_name)
        if camera is None:
            raise RuntimeError(f"Missing v4 review camera {camera_name}")
        scene.camera = camera
        path = output_dir / f"{view}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"Render failed: {path}")
        manifest.append(
            {
                "path": str(path),
                "view": view,
                "camera": camera_name,
                "size_bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "resolution": [scene.render.resolution_x, scene.render.resolution_y],
                "camera_matrix_world": [round(value, 7) for row in camera.matrix_world for value in row],
            }
        )
        print(f"AEGIS_V4_RENDERED={path}")


def render_iteration(scene: bpy.types.Scene, iteration: int, manifest: list[dict]) -> None:
    v3render.configure_render(scene, cfg.RENDER["resolution"], False)
    isolate(helmet=True, master=False, proxy=False)
    assign_material("design")
    render_views(
        scene,
        cfg.OUTPUT_DIR / "review_iterations" / f"iteration_{iteration:02d}" / "design",
        ("front", "back", "left", "right", "front_3q", "top"),
        manifest,
    )


def render_final(scene: bpy.types.Scene, manifest: list[dict]) -> None:
    v3render.configure_render(scene, cfg.RENDER["resolution"], False)
    isolate(helmet=True, master=False, proxy=False)
    assign_material("clay")
    render_views(scene, cfg.OUTPUT_DIR / "clay" / "helmet_only", cfg.REVIEW_VIEWS, manifest)

    isolate(helmet=True, master=False, proxy=False)
    assign_material("design")
    render_views(scene, cfg.OUTPUT_DIR / "design_preview" / "helmet_only", cfg.REVIEW_VIEWS, manifest)

    isolate(helmet=True, master=False, proxy=True)
    assign_material("clay")
    hidden = {"CrownFront", "Temple_L", "Cheek_L", "Jaw_L", "RearShell_L", "EarCover_L", "InnerShell"}
    for obj in collection_objects("HELMET_V3"):
        if obj.name in hidden or obj.get("feature_owner") in hidden or obj.name.endswith("_L"):
            obj.hide_render = True
    render_views(scene, cfg.OUTPUT_DIR / "with_head_proxy", cfg.REVIEW_VIEWS, manifest)

    isolate(helmet=True, master=False, proxy=False)
    assign_material("design")
    originals = {obj.name: obj.location.copy() for obj in collection_objects("HELMET_V3") if obj.type == "MESH"}
    owner_displacements: dict[str, Vector] = {}
    for obj in collection_objects("HELMET_V3"):
        if obj.type != "MESH" or obj.get("feature_owner"):
            continue
        direction = obj.location.copy()
        direction.z *= 0.45
        if direction.length < 0.01:
            direction = Vector((0.0, -1.0, 0.0))
        owner_displacements[obj.name] = direction.normalized() * 0.032
    for obj in collection_objects("HELMET_V3"):
        if obj.type != "MESH":
            continue
        owner = obj.get("feature_owner") or obj.name
        obj.location += owner_displacements.get(owner, Vector((0.0, -0.032, 0.0)))
    try:
        render_views(scene, cfg.OUTPUT_DIR / "exploded_sanity", ("front_3q",), manifest)
    finally:
        for name, location in originals.items():
            bpy.data.objects[name].location = location

    v3render.configure_render(scene, cfg.RENDER["silhouette_resolution"], True)
    isolate(helmet=True, master=False, proxy=False)
    assign_material("silhouette")
    render_views(scene, cfg.OUTPUT_DIR / "silhouette", ("front", "left", "right", "back", "top"), manifest)


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    if not cfg.BLEND_PATH.is_file():
        raise FileNotFoundError(f"Missing v4 blend: {cfg.BLEND_PATH}")
    bpy.ops.wm.open_mainfile(filepath=str(cfg.BLEND_PATH))
    scene = bpy.context.scene
    manifest: list[dict] = []
    if args.mode == "iteration":
        render_iteration(scene, args.iteration, manifest)
    elif args.mode == "silhouette":
        v3render.configure_render(scene, cfg.RENDER["silhouette_resolution"], True)
        isolate(helmet=True, master=False, proxy=False)
        assign_material("silhouette")
        render_views(scene, cfg.OUTPUT_DIR / "silhouette", ("front", "left", "right", "back", "top"), manifest)
    else:
        render_final(scene, manifest)
    log = {
        "script": str(Path(__file__).resolve()),
        "mode": args.mode,
        "iteration": args.iteration,
        "engine": scene.render.engine,
        "elapsed_seconds": round(time.perf_counter() - started, 4),
        "render_count": len(manifest),
        "renders": manifest,
    }
    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    name = f"iteration_{args.iteration}_render.json" if args.mode == "iteration" else ("final_render.json" if args.mode == "all" else "silhouette_render.json")
    (cfg.LOG_DIR / name).write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"AEGIS_V4_RENDER_OK mode={args.mode} count={len(manifest)}")


if __name__ == "__main__":
    main()

