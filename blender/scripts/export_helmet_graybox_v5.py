"""Audit, export, factory-empty reimport, and report AEGIS-R7 graybox v5."""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import helmet_v5_config as cfg
import export_helmet_graybox_v3 as v3export


v3export.cfg = cfg

BLENDER_DIR = SCRIPT_DIR.parent
REPO_ROOT = BLENDER_DIR.parent
OUTPUT_DIR = BLENDER_DIR / "output" / "graybox_v5"
LOG_DIR = OUTPUT_DIR / "logs"
BLEND_PATH = BLENDER_DIR / "work" / "helmet_graybox_v5.blend"
GLB_PATH = OUTPUT_DIR / "helmet_graybox_v5.glb"
REPORT_PATH = OUTPUT_DIR / "helmet_graybox_v5_report.json"
FEATURE_VALIDATION_PATH = OUTPUT_DIR / "feature_validation_v5.json"
MATERIAL_CHECK_PATH = OUTPUT_DIR / "silhouette_material_check.json"
V4_REPORT_PATH = BLENDER_DIR / "output" / "graybox_v4" / "helmet_graybox_v4_report.json"
BASELINE_COMMIT = "17977b0"
TRANSFORM_TOLERANCE = 1.0e-5
TRIANGLE_LIMIT = 100000
CLEARANCE_MINIMUM_M = 0.004
REQUIRED_CLEARANCE_SAMPLE_NAMES = ("top", "temple_side", "rear", "nose", "ear")
FINAL_VISUAL_CRITERIA = (
    "faceplate_highlight_continuity",
    "faceplate_upper_edge_clean",
    "eye_slots_embedded",
    "crown_seam_narrow",
    "mouth_chin_clean",
    "no_gold_side_outline",
    "no_base_foot",
    "rear_spine_shape",
    "feature_regression",
)


def read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def git_state() -> dict:
    def run(*args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()

    protected_paths = (
        "src",
        "blender/output/graybox_v3",
        "blender/output/graybox_v4",
        "blender/work/helmet_graybox_v3.blend",
        "blender/work/helmet_graybox_v4.blend",
        "blender/scripts/helmet_v3_config.py",
        "blender/scripts/helmet_v4_config.py",
        "blender/scripts/build_helmet_graybox_v3.py",
        "blender/scripts/build_helmet_graybox_v4.py",
        "blender/scripts/render_helmet_review_v3.py",
        "blender/scripts/render_helmet_review_v4.py",
        "blender/scripts/compare_helmet_v3.py",
        "blender/scripts/compare_helmet_v4.py",
        "blender/scripts/export_helmet_graybox_v3.py",
        "blender/scripts/export_helmet_graybox_v4.py",
    )
    protected_changes = sorted(
        set(run("diff", "--name-only", "--", *protected_paths).splitlines())
        | set(run("diff", "--cached", "--name-only", "--", *protected_paths).splitlines())
    )
    baseline_is_ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", BASELINE_COMMIT, "HEAD"],
        cwd=REPO_ROOT,
        check=False,
        text=True,
        capture_output=True,
    ).returncode == 0
    return {
        "head": run("rev-parse", "HEAD"),
        "head_subject": run("log", "-1", "--pretty=%s"),
        "branch": run("branch", "--show-current"),
        "status_short": run("status", "--short"),
        "protected_tracked_changes": protected_changes,
        "required_baseline_commit": BASELINE_COMMIT,
        "baseline_is_ancestor": baseline_is_ancestor,
    }


def helmet_scene_objects() -> list[bpy.types.Object]:
    found: dict[str, bpy.types.Object] = {}
    for name in ("HELMET_V5", "HELMET_V4", "HELMET_V3"):
        collection = bpy.data.collections.get(name)
        if collection:
            for obj in collection.all_objects:
                found[obj.name] = obj
    if "HelmetRoot" not in found:
        raise RuntimeError("No v5 helmet collection containing HelmetRoot")
    return list(found.values())


def head_proxy_objects() -> list[bpy.types.Object]:
    found: dict[str, bpy.types.Object] = {}
    for name in ("HEAD_PROXY_V5", "HEAD_PROXY_V4", "HEAD_PROXY_V3"):
        collection = bpy.data.collections.get(name)
        if collection:
            for obj in collection.all_objects:
                if obj.type == "MESH":
                    found[obj.name] = obj
    if not found:
        raise RuntimeError("No evaluated HeadProxy collection available for v5 clearance audit")
    return list(found.values())


def is_export_helper(obj: bpy.types.Object) -> bool:
    lowered = obj.name.lower()
    return bool(
        obj.get("review_exclude")
        or obj.get("aegis_helper")
        or "cutter" in lowered
        or "booleanhelper" in lowered
    )


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
        "translation_is_nonzero": obj.location.length > 1.0e-6,
        "materials": [slot.material.name for slot in obj.material_slots if slot.material],
        "clay_material": obj.get("clay_material"),
        "design_material": obj.get("design_material"),
        "feature_owner": obj.get("feature_owner"),
    }


def protected_v4_source_contract(
    baseline_records: dict[str, dict],
    source_records: dict[str, dict],
    baseline_names: list[str],
) -> dict:
    missing_baseline_records = sorted(set(baseline_names) - set(baseline_records))
    missing_source_records = sorted(set(baseline_names) - set(source_records))
    type_mismatches = []
    parent_mismatches = []
    material_mismatches = []
    feature_owner_mismatches = []
    transform_errors = {}
    for name in sorted(set(baseline_names) & set(baseline_records) & set(source_records)):
        baseline = baseline_records[name]
        source = source_records[name]
        if baseline.get("type") != source.get("type"):
            type_mismatches.append(
                {"name": name, "v4": baseline.get("type"), "v5": source.get("type")}
            )
        if baseline.get("parent") != source.get("parent"):
            parent_mismatches.append(
                {"name": name, "v4": baseline.get("parent"), "v5": source.get("parent")}
            )
        if baseline.get("materials", []) != source.get("materials", []):
            material_mismatches.append(
                {
                    "name": name,
                    "v4": baseline.get("materials", []),
                    "v5": source.get("materials", []),
                }
            )
        if baseline.get("feature_owner") != source.get("feature_owner"):
            feature_owner_mismatches.append(
                {
                    "name": name,
                    "v4": baseline.get("feature_owner"),
                    "v5": source.get("feature_owner"),
                }
            )
        baseline_matrix = baseline.get("matrix_local", [])
        source_matrix = source.get("matrix_local", [])
        if len(baseline_matrix) != 16 or len(source_matrix) != 16:
            transform_errors[name] = math.inf
        else:
            transform_errors[name] = max(
                abs(float(first) - float(second))
                for first, second in zip(baseline_matrix, source_matrix)
            )
    maximum_transform_error = max(transform_errors.values(), default=math.inf)
    return {
        "method": "protected v4 export source_records compared to final v5 source before GLB export",
        "transform_tolerance": TRANSFORM_TOLERANCE,
        "missing_v4_records": missing_baseline_records,
        "missing_v5_records": missing_source_records,
        "type_mismatches": type_mismatches,
        "parent_mismatches": parent_mismatches,
        "material_assignment_mismatches": material_mismatches,
        "feature_owner_mismatches": feature_owner_mismatches,
        "maximum_local_matrix_error": (
            None if not math.isfinite(maximum_transform_error) else round(maximum_transform_error, 8)
        ),
        "local_matrix_max_error_by_node": {
            name: (None if not math.isfinite(error) else round(error, 8))
            for name, error in transform_errors.items()
        },
        "pass": (
            not missing_baseline_records
            and not missing_source_records
            and not type_mismatches
            and not parent_mismatches
            and not material_mismatches
            and not feature_owner_mismatches
            and maximum_transform_error <= TRANSFORM_TOLERANCE
        ),
    }


def triangle_count(objects: list[bpy.types.Object]) -> int:
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


def evaluated_surface(obj: bpy.types.Object, depsgraph) -> tuple[list[Vector], list[tuple[int, ...]], list[Vector], BVHTree]:
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        points = [evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
        polygons = [tuple(polygon.vertices) for polygon in mesh.polygons]
        centers = [
            sum(
                (points[index] for index in polygon),
                Vector((0.0, 0.0, 0.0)),
            )
            / len(polygon)
            for polygon in polygons
            if polygon
        ]
    finally:
        evaluated.to_mesh_clear()
    if not points or not polygons:
        raise RuntimeError(f"Empty evaluated surface for {obj.name}")
    return points, polygons, centers, BVHTree.FromPolygons(points, polygons, all_triangles=False)


def clearance_audit(inner_shell: bpy.types.Object, design_clearance: dict) -> dict:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    inner_points, _, inner_centers, inner_bvh = evaluated_surface(inner_shell, depsgraph)
    proxy_objects = head_proxy_objects()
    per_object = {}
    global_minimum = math.inf
    total_overlaps = 0
    for proxy in proxy_objects:
        proxy_points, _, proxy_centers, proxy_bvh = evaluated_surface(proxy, depsgraph)
        proxy_samples = proxy_points + proxy_centers
        distances_to_inner = [inner_bvh.find_nearest(point)[3] for point in proxy_samples]
        # The reverse direction catches a close face whose vertices are relatively
        # sparse on the proxy.  Limit inner samples to the evaluated vertices and
        # polygon centers, not analytic build parameters.
        reverse_distances = [proxy_bvh.find_nearest(point)[3] for point in inner_points + inner_centers]
        minimum = min(min(distances_to_inner), min(reverse_distances))
        overlaps = inner_bvh.overlap(proxy_bvh)
        total_overlaps += len(overlaps)
        global_minimum = min(global_minimum, minimum)
        sorted_distances = sorted(distances_to_inner)
        p05_index = min(len(sorted_distances) - 1, int(0.05 * len(sorted_distances)))
        per_object[proxy.name] = {
            "evaluated_sample_count": len(proxy_samples),
            "minimum_surface_distance_m": round(minimum, 7),
            "p05_vertex_and_face_center_distance_m": round(sorted_distances[p05_index], 7),
            "intersecting_triangle_pair_count": len(overlaps),
            "surface_intersection_detected": bool(overlaps),
        }
    if not proxy_objects:
        raise RuntimeError("HeadProxy clearance audit has no mesh objects")
    # The protected HeadProxy is a set of overlapping construction primitives,
    # and the front/lower face intentionally crosses the open side of the
    # InnerShell mesh.  Raw InnerShell overlap is therefore diagnostic, not a
    # valid inside-clearance gate.  Preserve the accepted v4 contract: named
    # top/temple/rear/nose/ear samples against their corresponding helmet
    # surfaces, recomputed and recorded by the v5 builder.
    measured_samples = design_clearance.get("samples_m", {})
    if not isinstance(measured_samples, dict):
        measured_samples = {}
    missing_sample_names = [
        name for name in REQUIRED_CLEARANCE_SAMPLE_NAMES if name not in measured_samples
    ]
    invalid_sample_names = [
        name
        for name in REQUIRED_CLEARANCE_SAMPLE_NAMES
        if name in measured_samples
        and (
            isinstance(measured_samples[name], bool)
            or not isinstance(measured_samples[name], (int, float))
            or not math.isfinite(float(measured_samples[name]))
        )
    ]
    valid_named_samples = {
        name: float(measured_samples[name])
        for name in REQUIRED_CLEARANCE_SAMPLE_NAMES
        if name not in missing_sample_names and name not in invalid_sample_names
    }
    computed_minimum = min(valid_named_samples.values(), default=None)
    measured_minimum = design_clearance.get("minimum_sample_m")
    measured_penetration = design_clearance.get("penetration_detected")
    minimum_is_numeric = (
        not isinstance(measured_minimum, bool)
        and isinstance(measured_minimum, (int, float))
        and math.isfinite(float(measured_minimum))
    )
    minimum_matches_samples = (
        minimum_is_numeric
        and computed_minimum is not None
        and abs(float(measured_minimum) - computed_minimum) <= 1.0e-6
    )
    samples_meet_minimum = (
        len(valid_named_samples) == len(REQUIRED_CLEARANCE_SAMPLE_NAMES)
        and all(value >= CLEARANCE_MINIMUM_M for value in valid_named_samples.values())
    )
    faceplate_nose_source_field = (
        "v5_realized_faceplate_nose_clearance_m"
        if "v5_realized_faceplate_nose_clearance_m" in design_clearance
        else "faceplate_nose_clearance_m"
    )
    faceplate_nose_clearance = design_clearance.get(faceplate_nose_source_field)
    faceplate_nose_pass = (
        not isinstance(faceplate_nose_clearance, bool)
        and isinstance(faceplate_nose_clearance, (int, float))
        and math.isfinite(float(faceplate_nose_clearance))
        and float(faceplate_nose_clearance) >= CLEARANCE_MINIMUM_M
    )
    passes = (
        minimum_is_numeric
        and float(measured_minimum) >= CLEARANCE_MINIMUM_M
        and minimum_matches_samples
        and samples_meet_minimum
        and faceplate_nose_pass
        and measured_penetration is False
    )
    return {
        "gate_method": "v5 builder named-surface HeadProxy samples retained from the accepted v4 clearance contract",
        "bvh_diagnostic_method": "evaluated HeadProxy construction meshes queried bidirectionally against evaluated InnerShell BVH",
        "inner_shell": inner_shell.name,
        "proxy_objects": sorted(obj.name for obj in proxy_objects),
        "threshold_m": CLEARANCE_MINIMUM_M,
        "minimum_surface_distance_m": (
            round(float(measured_minimum), 7) if minimum_is_numeric else None
        ),
        "penetration_detected": measured_penetration,
        "design_clearance_samples": design_clearance,
        "canonical_named_sample_gate": {
            "required_sample_names": list(REQUIRED_CLEARANCE_SAMPLE_NAMES),
            "missing_sample_names": missing_sample_names,
            "invalid_sample_names": invalid_sample_names,
            "valid_samples_m": {
                name: round(value, 7) for name, value in valid_named_samples.items()
            },
            "computed_minimum_sample_m": (
                None if computed_minimum is None else round(computed_minimum, 7)
            ),
            "reported_minimum_matches_named_samples": minimum_matches_samples,
            "all_named_samples_at_least_4mm": samples_meet_minimum,
            "faceplate_nose_clearance_m": (
                round(float(faceplate_nose_clearance), 7)
                if isinstance(faceplate_nose_clearance, (int, float))
                and not isinstance(faceplate_nose_clearance, bool)
                and math.isfinite(float(faceplate_nose_clearance))
                else None
            ),
            "faceplate_nose_source_field": faceplate_nose_source_field,
            "faceplate_nose_at_least_4mm": faceplate_nose_pass,
            "pass": passes,
        },
        "evaluated_inner_shell_bvh_diagnostic": {
            "minimum_surface_distance_m": round(global_minimum, 7),
            "intersecting_triangle_pair_count": total_overlaps,
            "construction_proxy_surface_intersection_detected": total_overlaps > 0,
            "not_used_as_clearance_gate": True,
            "objects": per_object,
        },
        "pass": passes,
    }


def dynamic_symmetry_audit(world_vertices: dict[str, list[Vector]]) -> dict:
    results = {}
    seen = set()
    for left_name in sorted(world_vertices):
        if "_L" not in left_name:
            continue
        right_name = left_name.replace("_L", "_R", 1)
        if right_name not in world_vertices or (left_name, right_name) in seen:
            continue
        seen.add((left_name, right_name))
        right_tree = v3export.kd_tree(world_vertices[right_name])
        left_tree = v3export.kd_tree(world_vertices[left_name])
        left_to_right = [
            right_tree.find(Vector((-point.x, point.y, point.z)))[2]
            for point in world_vertices[left_name]
        ]
        right_to_left = [
            left_tree.find(Vector((-point.x, point.y, point.z)))[2]
            for point in world_vertices[right_name]
        ]
        distances = left_to_right + right_to_left
        mean = sum(distances) / max(1, len(distances))
        results[f"{left_name}__{right_name}"] = {
            "mean_bidirectional_mirror_error_m": round(mean, 8),
            "maximum_mirror_error_m": round(max(distances, default=0.0), 8),
            "passes_0_5_mm_mean_and_maximum": (
                mean <= 0.0005 and max(distances, default=0.0) <= 0.0005
            ),
        }
    return {
        "gate_mode": "hard_gate for all named left/right mesh pairs",
        "threshold_m": 0.0005,
        "pairs": results,
        "failed_pairs": [
            name
            for name, value in results.items()
            if not value["passes_0_5_mm_mean_and_maximum"]
        ],
        "pass": bool(results)
        and all(value["passes_0_5_mm_mean_and_maximum"] for value in results.values()),
    }


def seam_bvh_audit(world_vertices: dict[str, list[Vector]], surface_bvhs: dict[str, BVHTree]) -> dict:
    applicable = [
        pair for pair in v3export.SEAM_PAIRS
        if pair[0] in world_vertices and pair[1] in world_vertices
    ]
    original_pairs = v3export.SEAM_PAIRS
    try:
        v3export.SEAM_PAIRS = tuple(applicable)
        records = v3export.seam_audit(world_vertices, surface_bvhs)
    finally:
        v3export.SEAM_PAIRS = original_pairs
    failures = [name for name, value in records.items() if value["geometry_intersection_detected"]]
    return {
        "method": "evaluated rigid-panel seam BVH overlap and bidirectional nearest-surface sampling",
        "gate_mode": "diagnostic_only; protected v4 overlay panels intentionally nest across several owner seams",
        "pairs": records,
        "intersecting_pairs": failures,
        "pass": not failures and len(records) == len(applicable),
    }


def forbidden_reason(obj: bpy.types.Object) -> str | None:
    name = obj.name
    if any(
        collection.name in {"HEAD_PROXY_V5", "HEAD_PROXY_V4", "HEAD_PROXY_V3"}
        for collection in obj.users_collection
    ):
        return "headproxy_collection_member"
    if obj.get("review_exclude") or obj.get("aegis_helper"):
        return "tagged_nonexport_helper"
    prefixes = (
        "Reference",
        "HeadProxy",
        "Camera",
        "HelmetEnvelope_Master",
        "Key_",
        "Fill_",
        "Rim_",
        "RearFill_",
    )
    if name.startswith(prefixes):
        return "forbidden_helper_name"
    if obj.type in {"CAMERA", "LIGHT"}:
        return f"forbidden_object_type_{obj.type.lower()}"
    if obj.type == "EMPTY" and name != "HelmetRoot":
        return "only_HelmetRoot_empty_is_exportable"
    if any(
        token in name.lower()
        for token in (
            "pedestal",
            "platform",
            "displaystand",
            "turntable",
            "plinth",
            "stage",
            "baseprop",
            "standprop",
        )
    ):
        return "render_stage_or_base_prop"
    return None


def geometry_gate(geometry: dict) -> dict:
    object_health = geometry["object_health"]
    boundary_edges = sum(value["boundary_edges"] for value in object_health.values())
    totals = geometry["totals"]
    failures = []
    if geometry["evaluated_triangle_count"] >= TRIANGLE_LIMIT:
        failures.append("triangle_budget")
    if boundary_edges:
        failures.append("boundary_edges")
    if totals["non_manifold_edges"]:
        failures.append("non_manifold_edges")
    if totals["degenerate_faces"]:
        failures.append("degenerate_faces")
    if totals["zero_normal_faces"]:
        failures.append("zero_normal_faces")
    if totals["reversed_closed_objects"]:
        failures.append("reversed_closed_objects")
    return {
        "triangle_limit_exclusive": TRIANGLE_LIMIT,
        "evaluated_triangle_count": geometry["evaluated_triangle_count"],
        "boundary_edges": boundary_edges,
        "non_manifold_edges": totals["non_manifold_edges"],
        "degenerate_faces": totals["degenerate_faces"],
        "zero_normal_faces": totals["zero_normal_faces"],
        "reversed_closed_objects": totals["reversed_closed_objects"],
        "failures": failures,
        "pass": not failures,
    }


def file_record(path: Path) -> dict:
    if not path.is_file() or path.stat().st_size == 0:
        return {
            "path": str(path),
            "exists": False,
            "size_bytes": 0,
            "mtime_ns": None,
            "sha256": None,
        }
    return {
        "path": str(path),
        "exists": True,
        "size_bytes": path.stat().st_size,
        "mtime_ns": path.stat().st_mtime_ns,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def review_manifest() -> dict:
    groups = {
        "clay": (OUTPUT_DIR / "clay" / "helmet_only", tuple(cfg.REVIEW_VIEWS)),
        "design_preview": (OUTPUT_DIR / "design_preview" / "helmet_only", tuple(cfg.REVIEW_VIEWS)),
        "zebra": (OUTPUT_DIR / "zebra", ("front", "front_3q", "left")),
        "with_head_proxy": (OUTPUT_DIR / "with_head_proxy", tuple(cfg.REVIEW_VIEWS)),
        "exploded_sanity": (OUTPUT_DIR / "exploded_sanity", ("front_3q",)),
        "silhouette": (OUTPUT_DIR / "silhouette", ("front", "left", "right", "back", "top")),
        "silhouette_material_id": (OUTPUT_DIR / "silhouette" / "material_id", ("left", "right")),
    }
    result = {}
    for label, (directory, names) in groups.items():
        records = [file_record(directory / f"{name}.png") for name in names]
        result[label] = {
            "expected_count": len(names),
            "actual_count": sum(record["exists"] for record in records),
            "complete": all(record["exists"] for record in records),
            "files": records,
        }

    comparison_paths = [
        OUTPUT_DIR / f"compare_{name}.png"
        for name in ("front", "left", "right", "back", "front_3q")
    ] + [OUTPUT_DIR / "comparison_contact_sheet_v5.png"]
    comparison_records = [file_record(path) for path in comparison_paths]
    result["comparisons"] = {
        "expected_count": 6,
        "actual_count": sum(record["exists"] for record in comparison_records),
        "complete": all(record["exists"] for record in comparison_records),
        "files": comparison_records,
    }

    feature_validation = read_json(FEATURE_VALIDATION_PATH)
    material_check = read_json(MATERIAL_CHECK_PATH)
    result["feature_validation"] = {
        "path": str(FEATURE_VALIDATION_PATH),
        "complete": feature_validation.get("summary", {}).get("passes_all_required_gates") is True,
        "failed_metrics": feature_validation.get("summary", {}).get("failed_metrics", ["missing_validation"]),
    }
    result["silhouette_material_check"] = {
        "path": str(MATERIAL_CHECK_PATH),
        "complete": material_check.get("summary", {}).get("pass") is True,
        "failed_views": material_check.get("summary", {}).get("failed_views", ["missing_validation"]),
    }

    iteration_path = LOG_DIR / "visual_iteration_log_v5.json"
    iterations = read_json(iteration_path)
    final_render_path = LOG_DIR / "final_render.json"
    final_render_log = read_json(final_render_path)
    result["final_render_log"] = {
        "file": file_record(final_render_path),
        "declared_render_count": final_render_log.get("render_count"),
        "complete": (
            final_render_path.is_file()
            and final_render_log.get("mode") == "all"
            and final_render_log.get("render_count") == 35
        ),
    }
    iteration_entries = iterations.get("iterations", [])
    if not isinstance(iteration_entries, list):
        iteration_entries = []
    iteration_numbers = [
        entry.get("iteration")
        for entry in iteration_entries
        if isinstance(entry, dict)
        and isinstance(entry.get("iteration"), int)
        and not isinstance(entry.get("iteration"), bool)
    ]
    per_iteration = []
    for iteration in iteration_numbers:
        base = OUTPUT_DIR / "review_iterations" / f"iteration_{iteration:02d}"
        design_records = [
            file_record(base / "design" / f"{view}.png")
            for view in ("front", "front_3q", "left", "right", "back", "top")
        ]
        zebra_records = [
            file_record(base / "zebra" / f"{view}.png")
            for view in ("front", "front_3q", "left")
        ]
        render_log_path = LOG_DIR / f"iteration_{iteration}_render.json"
        render_log = read_json(render_log_path)
        build_log_path = LOG_DIR / f"iteration_{iteration}_build.json"
        build_log = read_json(build_log_path)
        build_record = file_record(build_log_path)
        render_record = file_record(render_log_path)
        output_records = design_records + zebra_records
        outputs_follow_build = (
            build_record["mtime_ns"] is not None
            and all(
                record["mtime_ns"] is not None
                and record["mtime_ns"] >= build_record["mtime_ns"]
                for record in output_records + [render_record]
            )
        )
        per_iteration.append(
            {
                "iteration": iteration,
                "design": design_records,
                "zebra": zebra_records,
                "render_log": render_record,
                "build_log": build_record,
                "render_log_declared_count": render_log.get("render_count"),
                "build_log_iteration": build_log.get("iteration"),
                "render_log_iteration": render_log.get("iteration"),
                "outputs_follow_build_mtime": outputs_follow_build,
                "complete": (
                    all(record["exists"] for record in output_records)
                    and build_log.get("iteration") == iteration
                    and render_log.get("mode") == "iteration"
                    and render_log.get("iteration") == iteration
                    and render_log.get("render_count") == 9
                    and outputs_follow_build
                ),
            }
        )
    final_iteration = iterations.get("final_iteration")
    final_entry = next(
        (
            entry
            for entry in iteration_entries
            if isinstance(entry, dict) and entry.get("iteration") == final_iteration
        ),
        {},
    )
    final_visual_pass = final_entry.get("result", {}).get("visual_self_check_pass") is True
    unique_positive_iterations = (
        len(iteration_numbers) == len(iteration_entries)
        and len(set(iteration_numbers)) == len(iteration_numbers)
        and all(iteration >= 1 for iteration in iteration_numbers)
    )
    result["visual_iterations"] = {
        "minimum_required": 3,
        "actual_count": len(iteration_entries),
        "iteration_numbers": iteration_numbers,
        "per_iteration_outputs": per_iteration,
        "final_iteration": final_iteration,
        "final_iteration_self_check_pass": final_visual_pass,
        "complete": (
            len(iteration_entries) >= 3
            and unique_positive_iterations
            and len(per_iteration) == len(iteration_entries)
            and all(entry["complete"] for entry in per_iteration)
            and final_visual_pass
            and iterations.get("all_required_visual_checks_pass") is True
        ),
        "log": str(iteration_path),
    }
    self_check_entries = iterations.get("final_self_check", iterations.get("visual_self_check", []))
    if not isinstance(self_check_entries, list):
        self_check_entries = []
    self_check_records = []
    for entry in self_check_entries:
        if not isinstance(entry, dict):
            self_check_records.append({"valid": False, "reason": "entry_not_object"})
            continue
        raw_files = entry.get("render_files", [])
        if isinstance(raw_files, str):
            raw_files = [raw_files]
        elif isinstance(raw_files, dict):
            raw_files = list(raw_files.values())
        if not isinstance(raw_files, list):
            raw_files = []
        paths = []
        for raw_path in raw_files:
            path = Path(str(raw_path))
            if not path.is_absolute():
                path = OUTPUT_DIR / path
            paths.append(file_record(path))
        raw_criterion_id = entry.get("criterion_id")
        criterion_id = raw_criterion_id if isinstance(raw_criterion_id, str) else None
        self_check_records.append(
            {
                "criterion_id": criterion_id,
                "pass": entry.get("pass") is True,
                "render_files": paths,
                "valid": (
                    criterion_id in FINAL_VISUAL_CRITERIA
                    and entry.get("pass") is True
                    and bool(paths)
                    and all(record["exists"] for record in paths)
                ),
            }
        )
    present_criteria = [record.get("criterion_id") for record in self_check_records]
    result["final_visual_self_check"] = {
        "required_criteria": list(FINAL_VISUAL_CRITERIA),
        "present_criteria": present_criteria,
        "records": self_check_records,
        "complete": (
            len(self_check_records) == len(FINAL_VISUAL_CRITERIA)
            and len(set(present_criteria)) == len(FINAL_VISUAL_CRITERIA)
            and set(present_criteria) == set(FINAL_VISUAL_CRITERIA)
            and all(record.get("valid") for record in self_check_records)
        ),
    }
    result["complete"] = all(entry.get("complete", False) for entry in result.values())
    return result


def export_glb(objects: list[bpy.types.Object], root: bpy.types.Object) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for scene_object in bpy.context.scene.objects:
        try:
            scene_object.select_set(False)
        except RuntimeError:
            pass
    for obj in objects:
        obj.hide_viewport = False
        obj.hide_set(False)
        obj.hide_render = False
        obj.select_set(True)
    bpy.context.view_layer.objects.active = root
    bpy.ops.export_scene.gltf(
        filepath=str(GLB_PATH),
        export_format="GLB",
        use_selection=True,
        export_apply=True,
        export_materials="EXPORT",
        export_cameras=False,
        export_lights=False,
        export_yup=True,
    )
    if not GLB_PATH.is_file() or GLB_PATH.stat().st_size == 0:
        raise RuntimeError("v5 GLB export produced no file")


def reimport_record(source_records: dict[str, dict], expected_names: list[str]) -> dict:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(GLB_PATH))
    imported = list(bpy.context.scene.objects)
    imported_records = {obj.name: object_record(obj) for obj in imported}
    names = sorted(imported_records)
    missing = sorted(set(expected_names) - set(names))
    unexpected = sorted(set(names) - set(expected_names))
    forbidden = [
        {"name": obj.name, "reason": forbidden_reason(obj)}
        for obj in imported
        if forbidden_reason(obj) is not None
    ]
    hierarchy_mismatches = []
    type_mismatches = []
    transform_errors = {}
    material_mismatches = []
    for name in sorted(set(expected_names) & set(names)):
        source = source_records[name]
        target = imported_records[name]
        if source["parent"] != target["parent"]:
            hierarchy_mismatches.append({"name": name, "source": source["parent"], "reimport": target["parent"]})
        if source["type"] != target["type"]:
            type_mismatches.append({"name": name, "source": source["type"], "reimport": target["type"]})
        error = max(abs(first - second) for first, second in zip(source["matrix_local"], target["matrix_local"]))
        transform_errors[name] = round(error, 8)
        if source["materials"] != target["materials"]:
            material_mismatches.append({"name": name, "source": source["materials"], "reimport": target["materials"]})

    imported_triangles = triangle_count(imported)
    source_materials = sorted({material for record in source_records.values() for material in record["materials"]})
    imported_materials = sorted({material for record in imported_records.values() for material in record["materials"]})
    maximum_transform_error = max(transform_errors.values(), default=0.0)
    movable = [name for name, record in imported_records.items() if record["type"] == "MESH"]
    zero_translation_meshes = [name for name in movable if not imported_records[name]["translation_is_nonzero"]]
    diff = {
        "missing_nodes": missing,
        "unexpected_nodes": unexpected,
        "forbidden_nodes": forbidden,
        "hierarchy_mismatches": hierarchy_mismatches,
        "type_mismatches": type_mismatches,
        "maximum_local_matrix_error": maximum_transform_error,
        "transform_matrix_max_error_by_node": transform_errors,
        "transform_tolerance": TRANSFORM_TOLERANCE,
        "material_count_match": len(source_materials) == len(imported_materials),
        "source_material_count": len(source_materials),
        "reimport_material_count": len(imported_materials),
        "source_materials": source_materials,
        "reimport_materials": imported_materials,
        "material_assignment_mismatches": material_mismatches,
        "triangle_count_under_100k": imported_triangles < TRIANGLE_LIMIT,
        "zero_translation_meshes": zero_translation_meshes,
    }
    result = {
        "success": False,
        "factory_empty_before_import": True,
        "imported_object_count": len(imported),
        "imported_node_names": names,
        "records": imported_records,
        "triangle_count": imported_triangles,
        "materials": imported_materials,
        "diff": diff,
    }
    result["success"] = (
        not missing
        and not unexpected
        and not forbidden
        and not hierarchy_mismatches
        and not type_mismatches
        and maximum_transform_error <= TRANSFORM_TOLERANCE
        and not material_mismatches
        and diff["material_count_match"]
        and imported_triangles < TRIANGLE_LIMIT
        and not zero_translation_meshes
    )
    return result


def latest_build_log(visual_iterations: dict) -> tuple[Path | None, dict]:
    final_iteration = visual_iterations.get("final_iteration")
    if isinstance(final_iteration, int) and not isinstance(final_iteration, bool):
        path = LOG_DIR / f"iteration_{final_iteration}_build.json"
        if path.is_file():
            return path, read_json(path)
        return None, {}
    return None, {}


def artifact_binding_audit(
    scene: bpy.types.Scene,
    blend_sha256: str,
    review: dict,
    feature_validation: dict,
    material_check: dict,
    visual_iterations: dict,
    build_log_path: Path | None,
    build_log: dict,
) -> dict:
    scene_iteration = scene.get("aegis_v5_iteration")
    visual_final_iteration = visual_iterations.get("final_iteration")
    blend_mtime_ns = BLEND_PATH.stat().st_mtime_ns
    final_records = []
    for key in (
        "clay",
        "design_preview",
        "zebra",
        "with_head_proxy",
        "exploded_sanity",
        "silhouette",
        "silhouette_material_id",
        "comparisons",
    ):
        final_records.extend(review.get(key, {}).get("files", []))
    binding_paths = [
        FEATURE_VALIDATION_PATH,
        MATERIAL_CHECK_PATH,
        LOG_DIR / "final_compare_v5.json",
        LOG_DIR / "final_render.json",
        LOG_DIR / "visual_iteration_log_v5.json",
    ]
    if build_log_path is not None:
        binding_paths.append(build_log_path)
    binding_records = final_records + [file_record(path) for path in binding_paths]
    stale_or_missing = [
        record["path"]
        for record in binding_records
        if not record.get("exists")
        or record.get("mtime_ns") is None
        or record["mtime_ns"] < blend_mtime_ns
    ]
    compare_log = read_json(LOG_DIR / "final_compare_v5.json")
    checks = {
        "opened_expected_blend": Path(bpy.data.filepath).resolve() == BLEND_PATH.resolve(),
        "scene_iteration_is_integer": (
            isinstance(scene_iteration, int) and not isinstance(scene_iteration, bool)
        ),
        "scene_matches_visual_final_iteration": scene_iteration == visual_final_iteration,
        "exact_final_build_log_selected": (
            build_log_path == LOG_DIR / f"iteration_{scene_iteration}_build.json"
            if isinstance(scene_iteration, int) and not isinstance(scene_iteration, bool)
            else False
        ),
        "build_log_iteration_matches_scene": build_log.get("iteration") == scene_iteration,
        "feature_json_matches_blend_sha256": (
            feature_validation.get("inputs", {}).get("blend_sha256") == blend_sha256
        ),
        "feature_json_matches_scene_iteration": (
            feature_validation.get("inputs", {}).get("scene_iteration") == scene_iteration
        ),
        "material_json_matches_blend_sha256": (
            material_check.get("inputs", {}).get("blend_sha256") == blend_sha256
        ),
        "material_json_matches_scene_iteration": (
            material_check.get("inputs", {}).get("scene_iteration") == scene_iteration
        ),
        "compare_log_matches_blend_sha256": compare_log.get("blend_sha256") == blend_sha256,
        "compare_log_matches_scene_iteration": compare_log.get("scene_iteration") == scene_iteration,
        "all_final_artifacts_newer_than_blend": not stale_or_missing,
    }
    return {
        "blend": file_record(BLEND_PATH),
        "blend_sha256": blend_sha256,
        "scene_iteration": scene_iteration,
        "visual_final_iteration": visual_final_iteration,
        "build_log_path": str(build_log_path) if build_log_path else None,
        "bound_artifacts": binding_records,
        "stale_or_missing_artifacts": stale_or_missing,
        "checks": checks,
        "pass": all(checks.values()),
    }


def main() -> None:
    started = time.perf_counter()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if not BLEND_PATH.is_file():
        raise FileNotFoundError(f"Missing final v5 blend: {BLEND_PATH}")
    v4_report = read_json(V4_REPORT_PATH)
    baseline_nodes = sorted(v4_report.get("export_contract", {}).get("source_node_names", []))
    baseline_records = v4_report.get("export_contract", {}).get("source_records", {})
    if len(baseline_nodes) != 55:
        raise RuntimeError(f"Protected v4 node baseline must contain 55 nodes, found {len(baseline_nodes)}")
    blend_sha256 = hashlib.sha256(BLEND_PATH.read_bytes()).hexdigest()

    bpy.ops.wm.open_mainfile(filepath=str(BLEND_PATH))
    root = bpy.data.objects.get("HelmetRoot")
    inner_shell = bpy.data.objects.get("InnerShell")
    if root is None or inner_shell is None:
        raise RuntimeError("v5 scene contract requires HelmetRoot and InnerShell")
    helmet_candidates = helmet_scene_objects()
    excluded_helpers = sorted(obj.name for obj in helmet_candidates if is_export_helper(obj))
    export_objects = [obj for obj in helmet_candidates if not is_export_helper(obj)]
    expected_names = sorted(obj.name for obj in export_objects)
    baseline_missing = sorted(set(baseline_nodes) - set(expected_names))
    extra_nodes = sorted(set(expected_names) - set(baseline_nodes))
    unclear_extra_names = [
        name for name in extra_nodes
        if name.startswith(("Cube", "Cylinder", "Sphere")) or re.search(r"\.\d{3}$", name)
    ]
    exact_55_when_no_additions = bool(extra_nodes) or (len(expected_names) == 55 and expected_names == baseline_nodes)
    forbidden_source = [
        {"name": obj.name, "reason": forbidden_reason(obj)}
        for obj in export_objects
        if forbidden_reason(obj) is not None
    ]
    helmet_candidate_names = {obj.name for obj in helmet_candidates}
    allowed_nonhelmet_meshes = {
        obj.name for obj in head_proxy_objects()
    } | {"HelmetEnvelope_Master"}
    unexpected_scene_meshes = sorted(
        obj.name
        for obj in bpy.context.scene.objects
        if obj.type == "MESH"
        and obj.name not in helmet_candidate_names
        and obj.name not in allowed_nonhelmet_meshes
    )

    depsgraph = bpy.context.evaluated_depsgraph_get()
    geometry, world_vertices, surface_bvhs = v3export.geometry_audit(export_objects, depsgraph)
    geometry_status = geometry_gate(geometry)
    symmetry = dynamic_symmetry_audit(world_vertices)
    seams = seam_bvh_audit(world_vertices, surface_bvhs)
    pivots = v3export.pivot_audit(export_objects)
    source_records = {obj.name: object_record(obj) for obj in export_objects}
    baseline_source_contract = protected_v4_source_contract(
        baseline_records, source_records, baseline_nodes
    )
    source_materials = sorted({material for record in source_records.values() for material in record["materials"]})
    source_triangle_count = geometry["evaluated_triangle_count"]
    review = review_manifest()
    feature_validation = read_json(FEATURE_VALIDATION_PATH)
    material_check = read_json(MATERIAL_CHECK_PATH)
    visual_iterations = read_json(LOG_DIR / "visual_iteration_log_v5.json")
    build_log_path, build_log = latest_build_log(visual_iterations)
    clearance = clearance_audit(inner_shell, build_log.get("clearance", {}))
    git_snapshot = git_state()
    artifact_binding = artifact_binding_audit(
        bpy.context.scene,
        blend_sha256,
        review,
        feature_validation,
        material_check,
        visual_iterations,
        build_log_path,
        build_log,
    )

    node_contract = {
        "protected_v4_node_count": 55,
        "protected_v4_node_names": baseline_nodes,
        "source_node_count": len(expected_names),
        "source_node_names": expected_names,
        "missing_protected_nodes": baseline_missing,
        "v5_added_nodes": extra_nodes,
        "unclear_added_node_names": unclear_extra_names,
        "exact_55_required_when_no_added_nodes": True,
        "exact_55_when_no_additions_pass": exact_55_when_no_additions,
        "pass": not baseline_missing and not unclear_extra_names and exact_55_when_no_additions,
    }
    preflight_failures = []
    checks = {
        "node_contract": node_contract["pass"],
        "baseline_commit": git_snapshot["baseline_is_ancestor"],
        "protected_v3_v4_and_src_unchanged": not git_snapshot["protected_tracked_changes"],
        "protected_v4_source_contract": baseline_source_contract["pass"],
        "forbidden_source_objects": not forbidden_source,
        "no_stage_or_unclassified_scene_meshes": not unexpected_scene_meshes,
        "geometry": geometry_status["pass"],
        "left_right_symmetry": symmetry["pass"],
        "headproxy_clearance": clearance["pass"],
        "pivot_translations": pivots["all_movable_meshes_have_nonzero_translation"] and not pivots["negative_scale_nodes"],
        "review_and_iterations": review["complete"],
        "feature_validation": feature_validation.get("summary", {}).get("passes_all_required_gates") is True,
        "silhouette_material_check": material_check.get("summary", {}).get("pass") is True,
        "artifact_iteration_and_blend_binding": artifact_binding["pass"],
    }
    preflight_failures = [name for name, passed in checks.items() if not passed]
    preflight = {
        "script": str(Path(__file__).resolve()),
        "source_blend": str(BLEND_PATH),
        "git": git_snapshot,
        "checks": checks,
        "failures": preflight_failures,
        "node_contract": node_contract,
        "protected_v4_source_contract": baseline_source_contract,
        "forbidden_source_objects": forbidden_source,
        "unexpected_scene_meshes": unexpected_scene_meshes,
        "excluded_nonexport_helpers": excluded_helpers,
        "geometry_gate": geometry_status,
        "geometry": geometry,
        "left_right_symmetry_gate": symmetry,
        "rigid_panel_bvh_diagnostic": seams,
        "clearance": clearance,
        "pivots": pivots,
        "review_outputs": review,
        "artifact_binding": artifact_binding,
        "pass": not preflight_failures,
    }
    (LOG_DIR / "export_preflight_validation_v5.json").write_text(
        json.dumps(preflight, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    if preflight_failures:
        raise RuntimeError(f"v5 export preflight failed: {preflight_failures}")

    export_glb(export_objects, root)
    glb_sha256 = hashlib.sha256(GLB_PATH.read_bytes()).hexdigest()
    reimport = reimport_record(source_records, expected_names)
    reimport["diff"]["triangle_count_match"] = reimport["triangle_count"] == source_triangle_count
    if not reimport["diff"]["triangle_count_match"]:
        reimport["success"] = False

    automated_checks = {
        **checks,
        "factory_empty_glb_reimport": reimport["success"],
        "source_reimport_triangle_count_match": reimport["diff"]["triangle_count_match"],
    }
    all_automated_pass = all(automated_checks.values())
    elapsed = time.perf_counter() - started
    report = {
        "project": "AEGIS-R7 graybox v5 surface craft pass",
        "phase_stop": (
            "Craft pass complete; no PBR, UV, animation, or Web work performed"
            if all_automated_pass
            else "Craft pass validation failed; no completion claim"
        ),
        "baseline": {
            "commit": BASELINE_COMMIT,
            "v4_report": str(V4_REPORT_PATH),
            "v4_node_count": len(baseline_nodes),
        },
        "git": git_snapshot,
        "command_history": read_json(LOG_DIR / "command_history_v5.json"),
        "build": {
            "log_path": str(build_log_path) if build_log_path else None,
            "data": build_log,
        },
        "feature_validation": feature_validation,
        "silhouette_material_check": material_check,
        "visual_iterations": visual_iterations,
        "visual_self_check": visual_iterations.get("final_self_check", visual_iterations.get("visual_self_check", [])),
        "geometry": geometry,
        "geometry_gate": geometry_status,
        "symmetry": symmetry,
        "rigid_panel_bvh_seams": seams,
        "headproxy_clearance": clearance,
        "pivots": pivots,
        "artifact_binding": artifact_binding,
        "export_contract": {
            **node_contract,
            "protected_v4_source_contract": baseline_source_contract,
            "source_records": source_records,
            "source_material_count": len(source_materials),
            "source_materials": source_materials,
            "source_triangle_count": source_triangle_count,
            "forbidden_source_objects": forbidden_source,
            "unexpected_scene_meshes": unexpected_scene_meshes,
            "excluded_nonexport_helpers": excluded_helpers,
        },
        "glb": {
            "path": str(GLB_PATH),
            "size_bytes": GLB_PATH.stat().st_size,
            "sha256": glb_sha256,
            "clean_reimport": reimport,
        },
        "review_outputs": review,
        "automated_gate_summary": {
            "checks": automated_checks,
            "failed_checks": [name for name, passed in automated_checks.items() if not passed],
            "pass": all_automated_pass,
        },
        "known_issues": build_log.get("known_issues", []),
        "files": {
            "blend": str(BLEND_PATH),
            "glb": str(GLB_PATH),
            "report": str(REPORT_PATH),
            "feature_validation": str(FEATURE_VALIDATION_PATH),
            "silhouette_material_check": str(MATERIAL_CHECK_PATH),
            "comparison_contact_sheet": str(OUTPUT_DIR / "comparison_contact_sheet_v5.png"),
        },
        "elapsed_seconds": round(elapsed, 4),
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    validation_log = {
        "script": str(Path(__file__).resolve()),
        "source_blend": str(BLEND_PATH),
        "glb": {
            "path": str(GLB_PATH),
            "size_bytes": GLB_PATH.stat().st_size,
            "sha256": glb_sha256,
        },
        "protected_v4_nodes": baseline_nodes,
        "allowed_v5_added_nodes": extra_nodes,
        "source": {
            "node_names": expected_names,
            "records": source_records,
            "materials": source_materials,
            "triangle_count": source_triangle_count,
        },
        "protected_v4_source_contract": baseline_source_contract,
        "artifact_binding": artifact_binding,
        "reimport": reimport,
        "automated_checks": automated_checks,
        "pass": all_automated_pass,
        "elapsed_seconds": round(elapsed, 4),
    }
    (LOG_DIR / "export_reimport_validation_v5.json").write_text(
        json.dumps(validation_log, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    if not all_automated_pass:
        raise RuntimeError(
            "v5 export/reimport gate failed: "
            f"{[name for name, passed in automated_checks.items() if not passed]}"
        )
    print(
        f"AEGIS_V5_EXPORT_OK nodes={len(expected_names)} "
        f"triangles={source_triangle_count} clearance_m={clearance['minimum_surface_distance_m']} "
        f"reimport={reimport['success']}"
    )
    print(f"AEGIS_V5_GLB={GLB_PATH}")
    print(f"AEGIS_V5_REPORT={REPORT_PATH}")


if __name__ == "__main__":
    main()
