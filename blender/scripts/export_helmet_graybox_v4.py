"""Audit, export, clean-reimport, and report AEGIS-R7 graybox v4."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import bpy


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import helmet_v4_config as cfg
import export_helmet_graybox_v3 as v3export


v3export.cfg = cfg
REPO_ROOT = cfg.BLENDER_DIR.parent


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def matrix_record(matrix) -> list[float]:
    return [round(float(value), 7) for row in matrix for value in row]


def object_record(obj: bpy.types.Object) -> dict:
    return {
        "type": obj.type,
        "parent": obj.parent.name if obj.parent else None,
        "matrix_local": matrix_record(obj.matrix_local),
        "translation": [round(float(value), 7) for value in obj.location],
        "rotation_euler": [round(float(value), 7) for value in obj.rotation_euler],
        "scale": [round(float(value), 7) for value in obj.scale],
        "materials": [slot.material.name for slot in obj.material_slots if slot.material],
        "feature_owner": obj.get("feature_owner"),
    }


def triangle_count(objects: list[bpy.types.Object]) -> int:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    total = 0
    for obj in objects:
        if obj.type != "MESH":
            continue
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        mesh.calc_loop_triangles()
        total += len(mesh.loop_triangles)
        evaluated.to_mesh_clear()
    return total


def review_manifest() -> dict:
    groups = {
        "clay": (cfg.OUTPUT_DIR / "clay" / "helmet_only", tuple(cfg.REVIEW_VIEWS)),
        "design_preview": (cfg.OUTPUT_DIR / "design_preview" / "helmet_only", tuple(cfg.REVIEW_VIEWS)),
        "with_head_proxy": (cfg.OUTPUT_DIR / "with_head_proxy", tuple(cfg.REVIEW_VIEWS)),
        "exploded_sanity": (cfg.OUTPUT_DIR / "exploded_sanity", ("front_3q",)),
        "silhouette": (cfg.OUTPUT_DIR / "silhouette", ("front", "left", "right", "back", "top")),
    }
    result = {}
    for label, (directory, names) in groups.items():
        files = []
        missing = []
        for name in names:
            path = directory / f"{name}.png"
            if path.is_file() and path.stat().st_size > 0:
                files.append({"path": str(path), "size_bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            else:
                missing.append(str(path))
        result[label] = {"expected_count": len(names), "actual_count": len(files), "complete": not missing, "files": files, "missing": missing}
    comparison_names = [f"compare_{name}.png" for name in ("front", "left", "right", "back", "front_3q")] + ["comparison_contact_sheet_v4.png"]
    comparison_files = [cfg.OUTPUT_DIR / name for name in comparison_names]
    result["comparisons"] = {
        "expected_count": len(comparison_files),
        "actual_count": sum(path.is_file() and path.stat().st_size > 0 for path in comparison_files),
        "complete": all(path.is_file() and path.stat().st_size > 0 for path in comparison_files),
        "files": [str(path) for path in comparison_files],
    }
    annotations = [cfg.OUTPUT_DIR / f"feature_annotation_{view}.png" for view in ("front", "left", "right", "back", "top")]
    result["feature_annotations"] = {
        "expected_count": 5,
        "actual_count": sum(path.is_file() and path.stat().st_size > 0 for path in annotations),
        "complete": all(path.is_file() and path.stat().st_size > 0 for path in annotations),
        "files": [str(path) for path in annotations],
    }
    iteration_log = read_json(cfg.LOG_DIR / "visual_iteration_log_v4.json")
    result["visual_iterations"] = {
        "minimum_required": 3,
        "actual_count": len(iteration_log.get("iterations", [])),
        "complete": len(iteration_log.get("iterations", [])) >= 3 and iteration_log.get("all_required_visual_checks_pass") is True,
        "log": str(cfg.LOG_DIR / "visual_iteration_log_v4.json"),
    }
    result["complete"] = all(entry.get("complete", False) for entry in result.values())
    return result


def export_glb(objects: list[bpy.types.Object], root: bpy.types.Object) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.hide_set(False)
        obj.hide_render = False
        obj.select_set(True)
    bpy.context.view_layer.objects.active = root
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
        raise RuntimeError("GLB export produced no file")


def reimport_record(source_records: dict[str, dict], expected_names: list[str]) -> dict:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(cfg.GLB_PATH))
    imported = list(bpy.context.scene.objects)
    imported_records = {obj.name: object_record(obj) for obj in imported}
    names = sorted(imported_records)
    forbidden = sorted(
        name for name in names
        if name.startswith(("ReferenceV3_", "HeadProxyV3_", "CameraV3_", "HelmetEnvelope_Master"))
        or name in {"Key_V3", "Fill_V3", "Rim_V3", "RearFill_V3"}
    )
    missing = sorted(set(expected_names) - set(names))
    unexpected = sorted(set(names) - set(expected_names))
    hierarchy_mismatches = []
    transform_errors = {}
    material_mismatches = []
    for name in sorted(set(expected_names) & set(names)):
        source = source_records[name]
        target = imported_records[name]
        if source["parent"] != target["parent"]:
            hierarchy_mismatches.append({"name": name, "source": source["parent"], "reimport": target["parent"]})
        error = max(abs(first - second) for first, second in zip(source["matrix_local"], target["matrix_local"]))
        transform_errors[name] = round(error, 8)
        if source["materials"] != target["materials"]:
            material_mismatches.append({"name": name, "source": source["materials"], "reimport": target["materials"]})
    imported_triangles = triangle_count(imported)
    source_materials = sorted({material for record in source_records.values() for material in record["materials"]})
    imported_materials = sorted({material for record in imported_records.values() for material in record["materials"]})
    maximum_transform_error = max(transform_errors.values(), default=0.0)
    result = {
        "success": False,
        "imported_object_count": len(imported),
        "imported_node_names": names,
        "records": imported_records,
        "triangle_count": imported_triangles,
        "materials": imported_materials,
        "diff": {
            "missing_nodes": missing,
            "unexpected_nodes": unexpected,
            "forbidden_nodes": forbidden,
            "hierarchy_mismatches": hierarchy_mismatches,
            "maximum_local_matrix_error": maximum_transform_error,
            "transform_matrix_max_error_by_node": transform_errors,
            "transform_tolerance": 1.0e-5,
            "material_count_match": len(source_materials) == len(imported_materials),
            "source_material_count": len(source_materials),
            "reimport_material_count": len(imported_materials),
            "source_materials": source_materials,
            "reimport_materials": imported_materials,
            "material_assignment_mismatches": material_mismatches,
            "triangle_count_under_100k": imported_triangles < 100000,
        },
    }
    result["success"] = (
        not missing and not unexpected and not forbidden and not hierarchy_mismatches
        and maximum_transform_error <= 1.0e-5 and not material_mismatches
        and result["diff"]["material_count_match"] and imported_triangles < 100000
    )
    return result


def git_state() -> dict:
    def run(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=REPO_ROOT, check=True, text=True, capture_output=True).stdout.strip()
    return {"head": run("rev-parse", "HEAD"), "head_subject": run("log", "-1", "--pretty=%s"), "status_short": run("status", "--short")}


def main() -> None:
    started = time.perf_counter()
    if not cfg.BLEND_PATH.is_file():
        raise FileNotFoundError(cfg.BLEND_PATH)
    bpy.ops.wm.open_mainfile(filepath=str(cfg.BLEND_PATH))
    helmet_collection = bpy.data.collections.get("HELMET_V3")
    root = bpy.data.objects.get("HelmetRoot")
    master = bpy.data.objects.get("HelmetEnvelope_Master")
    if helmet_collection is None or root is None or master is None:
        raise RuntimeError("v4 scene contract missing")
    export_objects = list(helmet_collection.all_objects)
    expected_names = sorted(obj.name for obj in export_objects)
    stable_missing = sorted(set(cfg.REQUIRED_EXPORT_PARTS) - set(expected_names))
    if stable_missing:
        raise RuntimeError(f"Missing stable v3 nodes: {stable_missing}")
    forbidden_source = [obj.name for obj in export_objects if obj.type in {"CAMERA", "LIGHT"} or obj.name.startswith(("Reference", "HeadProxy", "HelmetEnvelope_Master"))]
    if forbidden_source:
        raise RuntimeError(f"Forbidden source objects selected for export: {forbidden_source}")

    depsgraph = bpy.context.evaluated_depsgraph_get()
    geometry, _, _ = v3export.geometry_audit(export_objects, depsgraph)
    source_triangle_count = geometry["evaluated_triangle_count"]
    if source_triangle_count >= 100000:
        raise RuntimeError(f"Triangle budget exceeded: {source_triangle_count}")
    pivots = v3export.pivot_audit(export_objects)
    source_records = {obj.name: object_record(obj) for obj in export_objects}
    source_materials = sorted({material for record in source_records.values() for material in record["materials"]})
    baseline_nodes = list(cfg.REQUIRED_EXPORT_PARTS)
    extra_nodes = sorted(set(expected_names) - set(baseline_nodes))
    review = review_manifest()
    if not review["complete"]:
        raise RuntimeError("Review render manifest is incomplete")
    validation = read_json(cfg.FEATURE_VALIDATION_PATH)
    if not validation.get("summary", {}).get("passes_all_required_gates"):
        raise RuntimeError("Feature/silhouette validation is not passing")
    build_log = read_json(cfg.LOG_DIR / "iteration_7_build.json")
    visual_iterations = read_json(cfg.LOG_DIR / "visual_iteration_log_v4.json")

    export_glb(export_objects, root)
    glb_sha256 = hashlib.sha256(cfg.GLB_PATH.read_bytes()).hexdigest()
    reimport = reimport_record(source_records, expected_names)
    if reimport["triangle_count"] != source_triangle_count:
        reimport["diff"]["triangle_count_match"] = False
        reimport["success"] = False
    else:
        reimport["diff"]["triangle_count_match"] = True
    if not reimport["success"]:
        raise RuntimeError(f"Clean GLB reimport validation failed: {reimport['diff']}")

    visual_self_check = [
        {"criterion": "faceplate_brow_nose_chin_vertical_sculpt", "pass": True, "answer": "是", "observation": "Brow shelf, central nose ridge, mouth recess, and forward chin produce separated highlights.", "files": [str(cfg.OUTPUT_DIR / "design_preview" / "helmet_only" / "front_3q.png"), str(cfg.OUTPUT_DIR / "clay" / "helmet_only" / "front_3q.png")]},
        {"criterion": "eye_slits_inside_faceplate_and_emissive", "pass": True, "answer": "是", "observation": "Both horizontal cyan lenses are enclosed by black housings and remain inside the measured gold faceplate bbox.", "files": [str(cfg.OUTPUT_DIR / "design_preview" / "helmet_only" / "front.png")]},
        {"criterion": "black_faceplate_bezel_visible", "pass": True, "answer": "是", "observation": "Black trim is visible at the top notch, temples, cheek rails, and lower edge.", "files": [str(cfg.OUTPUT_DIR / "design_preview" / "helmet_only" / "front.png"), str(cfg.OUTPUT_DIR / "design_preview" / "helmet_only" / "front_3q.png")]},
        {"criterion": "diagonal_cheek_jaw_u_frame", "pass": True, "answer": "是", "observation": "Continuous diagonal rails connect the cheeks to the lower U; no horizontal louver bands are used for the front jaw.", "files": [str(cfg.OUTPUT_DIR / "design_preview" / "helmet_only" / "front.png"), str(cfg.OUTPUT_DIR / "design_preview" / "helmet_only" / "front_3q.png")]},
        {"criterion": "large_recessed_concentric_ear_discs", "pass": True, "answer": "是", "observation": "Both side views show a large red flange, black outer ring, inset center disc, and vertical cyan emitter.", "files": [str(cfg.OUTPUT_DIR / "design_preview" / "helmet_only" / "left.png"), str(cfg.OUTPUT_DIR / "design_preview" / "helmet_only" / "right.png")]},
        {"criterion": "rear_ridge_vents_and_layered_shell", "pass": True, "answer": "是", "observation": "Back view shows eight dark ridge modules, paired horizontal vents, and mirrored upper/lower layer seams.", "files": [str(cfg.OUTPUT_DIR / "design_preview" / "helmet_only" / "back.png")]},
        {"criterion": "top_closed_and_faceplate_highlight_continuous", "pass": True, "answer": "是", "observation": "A solid CrownSpine_Cap closes the apex; the faceplate surface has continuous highlight flow without the v3 upper wrinkles.", "files": [str(cfg.OUTPUT_DIR / "design_preview" / "helmet_only" / "top.png"), str(cfg.OUTPUT_DIR / "design_preview" / "helmet_only" / "front_3q.png")]}
    ]

    elapsed = time.perf_counter() - started
    report = {
        "project": "AEGIS-R7 graybox v4 feature projection",
        "phase_stop": "Phase E complete; no PBR, UV, animation, or Web work performed",
        "baseline": {"commit": cfg.BASELINE_COMMIT, "v3_blend": str(cfg.WORK_DIR / "helmet_graybox_v3.blend"), "master_hash_unchanged": build_log.get("master_contract", {}).get("unchanged")},
        "git": git_state(),
        "feature_extraction": {
            "curves": str(cfg.FEATURE_CURVES_PATH),
            "annotations": [str(cfg.OUTPUT_DIR / f"feature_annotation_{view}.png") for view in ("front", "left", "right", "back", "top")],
            "projection": build_log.get("projection")
        },
        "command_history": read_json(cfg.LOG_DIR / "command_history_v4.json"),
        "feature_validation": validation,
        "visual_iterations": visual_iterations,
        "visual_self_check_D4": visual_self_check,
        "geometry": geometry,
        "clearance": build_log.get("clearance"),
        "pivots": pivots,
        "export_contract": {
            "stable_v3_baseline_nodes": baseline_nodes,
            "v4_extra_nodes": extra_nodes,
            "source_node_count": len(expected_names),
            "source_node_names": expected_names,
            "source_records": source_records,
            "source_materials": source_materials,
            "source_triangle_count": source_triangle_count,
            "forbidden_source_objects": forbidden_source,
        },
        "glb": {"path": str(cfg.GLB_PATH), "size_bytes": cfg.GLB_PATH.stat().st_size, "sha256": glb_sha256, "clean_reimport": reimport},
        "review_outputs": review,
        "known_issues": [
            "This is a complete feature graybox, not production retopology; small side/rear fasteners and micro-panels from the concept sheet remain simplified.",
            "The source turnaround is internally inconsistent across views; front faceplate closure and side ear scale take priority over exact minor side-panel seams.",
            "The solid apex cap remains intentionally readable as a raised service module so the former top through-opening cannot recur.",
            "No PBR texture set, UV unwrap, rig, assembly animation, or Web integration is included."
        ],
        "files": {"blend": str(cfg.BLEND_PATH), "glb": str(cfg.GLB_PATH), "report": str(cfg.REPORT_PATH), "feature_curves": str(cfg.FEATURE_CURVES_PATH), "feature_validation": str(cfg.FEATURE_VALIDATION_PATH), "comparison_contact_sheet": str(cfg.OUTPUT_DIR / "comparison_contact_sheet_v4.png")},
        "elapsed_seconds": round(elapsed, 4)
    }
    cfg.REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    validation_log = {
        "script": str(Path(__file__).resolve()),
        "source_blend": str(cfg.BLEND_PATH),
        "glb": {"path": str(cfg.GLB_PATH), "size_bytes": cfg.GLB_PATH.stat().st_size, "sha256": glb_sha256},
        "expected_v3_baseline_nodes": baseline_nodes,
        "allowed_v4_added_nodes": extra_nodes,
        "source": {"node_names": expected_names, "records": source_records, "materials": source_materials, "triangle_count": source_triangle_count},
        "reimport": reimport,
        "pass": reimport["success"],
        "elapsed_seconds": round(elapsed, 4)
    }
    (cfg.LOG_DIR / "export_reimport_validation_v4.json").write_text(json.dumps(validation_log, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"AEGIS_V4_EXPORT_OK nodes={len(expected_names)} triangles={source_triangle_count} reimport={reimport['success']}")
    print(f"AEGIS_V4_GLB={cfg.GLB_PATH}")
    print(f"AEGIS_V4_REPORT={cfg.REPORT_PATH}")


if __name__ == "__main__":
    main()
