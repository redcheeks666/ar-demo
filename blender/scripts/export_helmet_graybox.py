"""Export the AEGIS-R7 helmet-only GLB and write its validation report."""

from __future__ import annotations

import json
import platform
import sys
import time
from pathlib import Path

import bpy
from mathutils import Vector


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import helmet_config as cfg


def read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def reference_dimensions() -> dict[str, list[int]]:
    dimensions = {}
    for name, path in cfg.REFERENCE_PATHS.items():
        image = bpy.data.images.load(str(path), check_existing=False)
        dimensions[name] = [int(image.size[0]), int(image.size[1])]
        bpy.data.images.remove(image)
    return dimensions


def evaluated_statistics(objects: list[bpy.types.Object]) -> tuple[list[float], int]:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    minimum = Vector((float("inf"), float("inf"), float("inf")))
    maximum = Vector((float("-inf"), float("-inf"), float("-inf")))
    triangle_count = 0
    for obj in objects:
        if obj.type != "MESH":
            continue
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            mesh.calc_loop_triangles()
            triangle_count += len(mesh.loop_triangles)
            for vertex in mesh.vertices:
                world_vertex = evaluated.matrix_world @ vertex.co
                for axis in range(3):
                    minimum[axis] = min(minimum[axis], world_vertex[axis])
                    maximum[axis] = max(maximum[axis], world_vertex[axis])
        finally:
            evaluated.to_mesh_clear()
    dimensions = maximum - minimum
    return [round(float(value), 6) for value in dimensions], triangle_count


def evaluated_dimensions(objects: list[bpy.types.Object]) -> list[float]:
    """Evaluate bounds without using object transforms or unapplied modifiers heuristically."""
    dimensions, _ = evaluated_statistics(objects)
    return dimensions


def collect_review_images() -> list[Path]:
    paths = []
    for directory in (cfg.HELMET_ONLY_DIR, cfg.WITH_HEAD_DIR):
        for output_name in cfg.REVIEW_NAMES:
            path = directory / f"{output_name}.png"
            if path.is_file() and path.stat().st_size > 0:
                paths.append(path)
    return paths


def export_glb(export_objects: list[bpy.types.Object], root: bpy.types.Object) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in export_objects:
        obj.hide_set(False)
        obj.select_set(True)
    bpy.context.view_layer.objects.active = root
    cfg.GLB_PATH.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=str(cfg.GLB_PATH),
        export_format="GLB",
        use_selection=True,
        export_apply=True,
        export_materials="EXPORT",
        export_cameras=False,
        export_lights=False,
        export_yup=True,
    )
    if not cfg.GLB_PATH.is_file() or cfg.GLB_PATH.stat().st_size == 0:
        raise RuntimeError(f"GLB export failed: {cfg.GLB_PATH}")


def main() -> None:
    started = time.perf_counter()
    if bpy.data.objects.get("HelmetRoot") is None:
        if not cfg.BLEND_PATH.is_file():
            raise FileNotFoundError(f"Graybox Blend is missing: {cfg.BLEND_PATH}")
        bpy.ops.wm.open_mainfile(filepath=str(cfg.BLEND_PATH))

    helmet_collection = bpy.data.collections.get("HELMET")
    root = bpy.data.objects.get("HelmetRoot")
    if helmet_collection is None or root is None:
        raise RuntimeError("HELMET collection or HelmetRoot is missing")

    export_objects = list(helmet_collection.all_objects)
    names = [obj.name for obj in export_objects]
    missing_required = [name for name in cfg.REQUIRED_PARTS if name not in names]
    if missing_required:
        raise RuntimeError(f"Required export nodes are missing: {missing_required}")

    negative_scale = [
        obj.name
        for obj in export_objects
        if obj.matrix_world.to_3x3().determinant() < 0.0
        or any(component < 0.0 for component in obj.scale)
    ]
    duplicate_names = sorted({name for name in names if names.count(name) > 1})
    duplicate_suffix_names = sorted(name for name in names if ".00" in name)
    missing_material = sorted(
        obj.name
        for obj in export_objects
        if obj.type == "MESH" and len(obj.data.materials) == 0
    )
    abnormal_transforms = sorted(
        obj.name
        for obj in export_objects
        if obj.type == "MESH" and any(abs(value - 1.0) > 1.0e-6 for value in obj.scale)
    )

    export_glb(export_objects, root)
    bbox_dimensions, triangle_count = evaluated_statistics(export_objects)
    main_body_objects = [
        obj
        for obj in export_objects
        if obj.type == "MESH"
        and not obj.name.startswith("EarCover_")
        and not obj.name.startswith("EarCore_")
    ]
    main_body_dimensions = evaluated_dimensions(main_body_objects)
    evaluated_body_ratio = round(main_body_dimensions[2] / main_body_dimensions[0], 6)
    unique_materials = sorted(
        {
            slot.material.name
            for obj in export_objects
            if obj.type == "MESH"
            for slot in obj.material_slots
            if slot.material is not None
        }
    )
    review_images = collect_review_images()
    build_metrics = read_json(cfg.LOG_DIR / "build_metrics.json")
    render_metrics = read_json(cfg.LOG_DIR / "render_metrics.json")
    export_elapsed = time.perf_counter() - started
    total_elapsed = (
        float(build_metrics.get("elapsed_seconds", 0.0))
        + float(render_metrics.get("elapsed_seconds", 0.0))
        + export_elapsed
    )

    report = {
        "project": "AEGIS-R7 graybox v1",
        "operating_system": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "platform": platform.platform(),
        },
        "blender": {
            "executable": bpy.app.binary_path,
            "version": bpy.app.version_string,
            "version_tuple": list(bpy.app.version),
            "build_hash": bpy.app.build_hash.decode("utf-8"),
            "build_branch": bpy.app.build_branch.decode("utf-8"),
        },
        "reference_image_dimensions_px": reference_dimensions(),
        "helmet_overall_bounding_box_m": {
            "width_x": bbox_dimensions[0],
            "depth_y": bbox_dimensions[1],
            "height_z": bbox_dimensions[2],
        },
        "main_body_dimensions_m": {
            "evaluated_height": main_body_dimensions[2],
            "evaluated_width_excluding_ear_covers": main_body_dimensions[0],
            "evaluated_depth": main_body_dimensions[1],
            "evaluated_height_to_width_ratio": evaluated_body_ratio,
            "configured_nominal_height": cfg.HELMET["body_height"],
            "configured_nominal_width": cfg.HELMET["body_width"],
            "configured_height_to_width_ratio": round(
                cfg.HELMET["body_height"] / cfg.HELMET["body_width"], 6
            ),
            "target_height_to_width_ratio": cfg.HELMET["body_ratio_target"],
            "ear_covers_excluded": True,
        },
        "scene_object_count": len(bpy.data.objects),
        "export_object_count": len(export_objects),
        "export_node_names": sorted(names),
        "evaluated_triangle_count": triangle_count,
        "material_count": len(unique_materials),
        "material_names": unique_materials,
        "negative_scale": {"exists": bool(negative_scale), "objects": negative_scale},
        "duplicate_node_names": {
            "exists": bool(duplicate_names or duplicate_suffix_names),
            "exact_duplicates": duplicate_names,
            "automatic_suffix_names": duplicate_suffix_names,
        },
        "missing_material": {"exists": bool(missing_material), "objects": missing_material},
        "abnormal_export_transforms": {
            "exists": bool(abnormal_transforms),
            "objects": abnormal_transforms,
            "criterion": "non-unit object scale on exported mesh",
        },
        "files": {
            "glb": str(cfg.GLB_PATH),
            "glb_size_bytes": cfg.GLB_PATH.stat().st_size,
            "blend": str(cfg.BLEND_PATH),
            "blend_size_bytes": cfg.BLEND_PATH.stat().st_size,
        },
        "review": {
            "expected_image_count": 16,
            "actual_image_count": len(review_images),
            "all_expected_images_present": len(review_images) == 16,
            "images": [str(path) for path in review_images],
        },
        "script_execution_seconds": {
            "build": build_metrics.get("elapsed_seconds"),
            "render": render_metrics.get("elapsed_seconds"),
            "export_and_report": round(export_elapsed, 4),
            "total": round(total_elapsed, 4),
        },
        "known_issues": [
            "Low-poly anatomical head proxy is for clearance review only.",
            "Panel seams and mechanical pivots are first-pass placeholders.",
            "Eye recesses use ring-and-inset graybox topology rather than production boolean/support-loop topology.",
            "No production materials, micro-details, animation, or rigging are included.",
        ],
    }
    cfg.REPORT_PATH.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (cfg.LOG_DIR / "export_metrics.json").write_text(
        json.dumps(
            {
                "script": str(Path(__file__).resolve()),
                "elapsed_seconds": round(export_elapsed, 4),
                "glb_path": str(cfg.GLB_PATH),
                "report_path": str(cfg.REPORT_PATH),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"AEGIS_EXPORT_OK nodes={len(export_objects)} triangles={triangle_count}")
    print(f"AEGIS_GLB={cfg.GLB_PATH}")
    print(f"AEGIS_REPORT={cfg.REPORT_PATH}")


if __name__ == "__main__":
    main()
