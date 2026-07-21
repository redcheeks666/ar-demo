"""Compare v4 to references and measure realized feature geometry."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import bpy
import numpy as np
from bpy_extras.object_utils import world_to_camera_view


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import helmet_v4_config as cfg
import compare_helmet_v3 as v3compare


v3compare.cfg = cfg


def parse_args() -> argparse.Namespace:
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--iteration", type=int, default=7)
    return parser.parse_args(arguments)


def evaluated_world_vertices(obj: bpy.types.Object) -> list:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    matrix = obj.matrix_world
    vertices = [matrix @ vertex.co for vertex in mesh.vertices]
    evaluated.to_mesh_clear()
    return vertices


def projected_pixels(scene: bpy.types.Scene, camera: bpy.types.Object, objects: list[bpy.types.Object], size: int = 512) -> np.ndarray:
    points = []
    for obj in objects:
        for coordinate in evaluated_world_vertices(obj):
            ndc = world_to_camera_view(scene, camera, coordinate)
            points.append((float(ndc.x * size), float((1.0 - ndc.y) * size)))
    if not points:
        raise RuntimeError("No projected mesh points")
    return np.asarray(points, dtype=np.float64)


def point_bbox(points: np.ndarray) -> tuple[float, float, float, float]:
    return (float(points[:, 0].min()), float(points[:, 1].min()), float(points[:, 0].max()), float(points[:, 1].max()))


def normalized_center_and_size(points: np.ndarray, helmet_bbox: tuple[float, float, float, float]) -> dict:
    bbox = point_bbox(points)
    height = helmet_bbox[3] - helmet_bbox[1]
    center_x = 0.5 * (bbox[0] + bbox[2])
    center_y = 0.5 * (bbox[1] + bbox[3])
    helmet_center_x = 0.5 * (helmet_bbox[0] + helmet_bbox[2])
    return {
        "bbox_px": [round(value, 5) for value in bbox],
        "center_normalized_head_height": [
            (center_x - helmet_center_x) / height,
            (center_y - helmet_bbox[1]) / height,
        ],
        "width_normalized_head_height": (bbox[2] - bbox[0]) / height,
        "height_normalized_head_height": (bbox[3] - bbox[1]) / height,
    }


def metric_entry(reference, actual, error: float, threshold: float, unit: str, relative: bool = False) -> dict:
    return {
        "reference": reference,
        "actual": actual,
        "error": round(float(error), 6),
        "error_percent": round(float(error) * 100.0, 4),
        "threshold_percent": threshold,
        "unit": unit,
        "pass": float(error) * 100.0 <= threshold + 1.0e-8,
        "relative_to_feature": relative,
    }


def feature_validation(scene: bpy.types.Scene, feature_data: dict, iteration: int) -> dict:
    helmet_objects = [obj for obj in bpy.data.collections["HELMET_V3"].all_objects if obj.type == "MESH"]
    cameras = {view: bpy.data.objects[cfg.CAMERAS[view][0]] for view in ("front", "left", "right", "back", "top")}
    projected_all = {view: projected_pixels(scene, camera, helmet_objects) for view, camera in cameras.items()}
    helmet_bboxes = {view: point_bbox(points) for view, points in projected_all.items()}
    metrics = {}

    eye_center_errors = []
    eye_length_errors = []
    eye_details = {}
    for side, label in (("L", "left"), ("R", "right")):
        points = projected_pixels(scene, cameras["front"], [bpy.data.objects[f"EyeLens_{side}"]])
        actual = normalized_center_and_size(points, helmet_bboxes["front"])
        reference = feature_data["views"]["front"]["features"][f"eye_{label}_metrics"]
        reference_center = reference["center_normalized_head_height"]
        center_error = math.dist(reference_center, actual["center_normalized_head_height"])
        length_error = abs(actual["width_normalized_head_height"] - reference["length_head_height"]) / reference["length_head_height"]
        eye_center_errors.append(center_error)
        eye_length_errors.append(length_error)
        eye_details[label] = {"reference": reference, "actual_from_evaluated_geometry": actual, "center_error": center_error, "length_relative_error": length_error}
    metrics["eye_slit_center"] = metric_entry(
        {side: detail["reference"]["center_normalized_head_height"] for side, detail in eye_details.items()},
        {side: detail["actual_from_evaluated_geometry"]["center_normalized_head_height"] for side, detail in eye_details.items()},
        max(eye_center_errors), cfg.FEATURE_TOLERANCES_PERCENT["eye_center"], "percent_reference_helmet_height_worst_side",
    )
    metrics["eye_slit_center"]["per_side"] = eye_details
    metrics["eye_slit_length"] = metric_entry(
        {side: detail["reference"]["length_head_height"] for side, detail in eye_details.items()},
        {side: detail["actual_from_evaluated_geometry"]["width_normalized_head_height"] for side, detail in eye_details.items()},
        max(eye_length_errors), cfg.FEATURE_TOLERANCES_PERCENT["eye_length_relative"], "percent_feature_length_worst_side", True,
    )

    ear_center_errors = []
    ear_radius_errors = []
    ear_details = {}
    for side, view in (("L", "left"), ("R", "right")):
        points = projected_pixels(scene, cameras[view], [bpy.data.objects[f"EarCover_{side}"]])
        actual = normalized_center_and_size(points, helmet_bboxes[view])
        reference = feature_data["views"][view]["features"]["ear_circle_samples"]
        reference_center = reference["fit_center_normalized_head_height"]
        actual_radius = 0.5 * max(actual["width_normalized_head_height"], actual["height_normalized_head_height"])
        center_error = math.dist(reference_center, actual["center_normalized_head_height"])
        radius_error = abs(actual_radius - reference["fit_radius_head_height"]) / reference["fit_radius_head_height"]
        ear_center_errors.append(center_error)
        ear_radius_errors.append(radius_error)
        ear_details[view] = {
            "reference_center": reference_center,
            "reference_radius": reference["fit_radius_head_height"],
            "actual_from_evaluated_geometry": actual,
            "actual_radius": actual_radius,
            "center_error": center_error,
            "radius_relative_error": radius_error,
        }
    metrics["ear_disc_center"] = metric_entry(
        {view: detail["reference_center"] for view, detail in ear_details.items()},
        {view: detail["actual_from_evaluated_geometry"]["center_normalized_head_height"] for view, detail in ear_details.items()},
        max(ear_center_errors), cfg.FEATURE_TOLERANCES_PERCENT["ear_center"], "percent_reference_helmet_height_worst_side",
    )
    metrics["ear_disc_center"]["per_side"] = ear_details
    metrics["ear_disc_radius"] = metric_entry(
        {view: detail["reference_radius"] for view, detail in ear_details.items()},
        {view: detail["actual_radius"] for view, detail in ear_details.items()},
        max(ear_radius_errors), cfg.FEATURE_TOLERANCES_PERCENT["ear_radius_relative"], "percent_feature_radius_worst_side", True,
    )

    faceplate_points = projected_pixels(scene, cameras["front"], [bpy.data.objects["Faceplate"]])
    faceplate_actual = normalized_center_and_size(faceplate_points, helmet_bboxes["front"])
    faceplate_reference = feature_data["views"]["front"]["features"]["faceplate_metrics"]
    width_error = abs(faceplate_actual["width_normalized_head_height"] - faceplate_reference["max_width_head_height"])
    metrics["faceplate_max_width"] = metric_entry(
        faceplate_reference["max_width_head_height"], faceplate_actual["width_normalized_head_height"], width_error,
        cfg.FEATURE_TOLERANCES_PERCENT["faceplate_max_width"], "percent_reference_helmet_height",
    )
    actual_chin = (faceplate_actual["bbox_px"][3] - helmet_bboxes["front"][1]) / (helmet_bboxes["front"][3] - helmet_bboxes["front"][1])
    chin_error = abs(actual_chin - faceplate_reference["chin_tip_from_helmet_top_head_height"])
    metrics["faceplate_chin_tip_height"] = metric_entry(
        faceplate_reference["chin_tip_from_helmet_top_head_height"], actual_chin, chin_error,
        cfg.FEATURE_TOLERANCES_PERCENT["chin_tip_height"], "percent_reference_helmet_height",
    )

    spine_objects = [obj for obj in helmet_objects if obj.name.startswith("RearSpine_")]
    spine_points = projected_pixels(scene, cameras["back"], spine_objects)
    spine_actual = normalized_center_and_size(spine_points, helmet_bboxes["back"])["width_normalized_head_height"]
    spine_reference = feature_data["views"]["back"]["features"]["spine_metrics"]["median_width_head_height"]
    spine_error = abs(spine_actual - spine_reference) / spine_reference
    metrics["crown_rear_spine_width"] = metric_entry(
        spine_reference, spine_actual, spine_error, cfg.FEATURE_TOLERANCES_PERCENT["spine_width_relative"], "percent_feature_width", True,
    )

    faceplate_bbox = faceplate_actual["bbox_px"]
    lens_inside = True
    inside_details = {}
    for side in ("L", "R"):
        lens_points = projected_pixels(scene, cameras["front"], [bpy.data.objects[f"EyeLens_{side}"]])
        lens_bbox = point_bbox(lens_points)
        side_inside = (
            lens_bbox[0] >= faceplate_bbox[0] - 1.0
            and lens_bbox[2] <= faceplate_bbox[2] + 1.0
            and lens_bbox[1] >= faceplate_bbox[1] - 1.0
            and lens_bbox[3] <= faceplate_bbox[3] + 1.0
        )
        lens_inside = lens_inside and side_inside
        inside_details[side] = {"lens_bbox": list(lens_bbox), "inside_faceplate_bbox": side_inside}
    build_log = json.loads((cfg.LOG_DIR / f"iteration_{iteration}_build.json").read_text(encoding="utf-8"))
    structural = {
        "eye_lens_inside_faceplate_boundary": {"pass": lens_inside, "details": inside_details},
        "side_depth_profiles_at_least_12_samples": {
            "pass": all(feature_data["sampling_summary"]["side_depth_samples"][side] >= 12 for side in ("left", "right")),
            "counts": feature_data["sampling_summary"]["side_depth_samples"],
        },
        "projection_hit_ratio": {"pass": build_log["projection"]["hit_ratio"] == 1.0, "value": build_log["projection"]["hit_ratio"], "surface_hits": build_log["projection"]["surface_hits"]},
        "top_center_closed": {"pass": bpy.data.objects.get("CrownSpine_Cap") is not None, "closure_object": "CrownSpine_Cap"},
        "minimum_headproxy_clearance": {
            "pass": build_log["clearance"]["minimum_sample_m"] >= 0.004,
            "actual_m": build_log["clearance"]["minimum_sample_m"],
            "threshold_m": 0.004,
            "penetration_detected": build_log["clearance"]["penetration_detected"],
        },
    }
    return {
        "schema_version": "1.0",
        "project": "AEGIS-R7 graybox v4",
        "inputs": {"feature_curves": str(cfg.FEATURE_CURVES_PATH), "blend": str(cfg.BLEND_PATH), "reference_measurements": str(cfg.MEASUREMENTS_PATH)},
        "measurement_method": {"reference": "raw sampled crop pixels", "actual": "evaluated mesh vertices projected through final Blender review cameras", "uses_build_parameters_as_actual": False},
        "registration": {"method": "single per-view helmet bbox height and center, no per-feature fit", "helmet_bboxes_px": {view: [round(value, 5) for value in bbox] for view, bbox in helmet_bboxes.items()}},
        "metrics": metrics,
        "structural_checks": structural,
        "summary": {
            "required_metrics": len(metrics),
            "passed_metrics": sum(1 for metric in metrics.values() if metric["pass"]),
            "failed_metrics": [name for name, metric in metrics.items() if not metric["pass"]],
            "structural_failures": [name for name, check in structural.items() if not check["pass"]],
        },
    }


def silhouette_validation() -> dict:
    metrics = {}
    overlays = {}
    for view in ("front", "left", "right"):
        reference = v3compare.normalized_mask(v3compare.reference_mask(view))
        model = v3compare.normalized_mask(v3compare.rendered_mask(cfg.OUTPUT_DIR / "silhouette" / f"{view}.png"))
        metrics[view] = v3compare.silhouette_metrics(reference, model)
        overlays[view] = v3compare.overlay(reference, model)
        v3compare.save_rgba(cfg.OUTPUT_DIR / f"overlay_{view}.png", overlays[view])
    passes = metrics["front"]["iou"] >= 0.90 and metrics["left"]["iou"] >= 0.85 and metrics["right"]["iou"] >= 0.85
    return {"metrics": metrics, "thresholds": {"front_iou_min": 0.90, "left_iou_min": 0.85, "right_iou_min": 0.85}, "pass": passes}


def comparison_outputs() -> list[str]:
    definitions = {
        "front": "front",
        "left": "left",
        "right": "right",
        "back": "back",
        "front_3q": "closed_front_3q",
    }
    rows = []
    outputs = []
    for output_name, reference_name in definitions.items():
        reference = cfg.DERIVED_REFERENCE_DIR / f"{reference_name}_crop.png"
        annotation = cfg.OUTPUT_DIR / f"feature_annotation_{output_name}.png"
        if output_name == "front_3q":
            annotation = reference
        images = [
            v3compare.load_rgba(reference),
            v3compare.load_rgba(annotation),
            v3compare.load_rgba(cfg.BLENDER_DIR / "output" / "graybox_v3" / "design_preview" / "helmet_only" / f"{output_name}.png"),
            v3compare.load_rgba(cfg.OUTPUT_DIR / "clay" / "helmet_only" / f"{output_name}.png"),
            v3compare.load_rgba(cfg.OUTPUT_DIR / "design_preview" / "helmet_only" / f"{output_name}.png"),
        ]
        row = v3compare.comparison_row(images, size=320)
        rows.append(row)
        path = cfg.OUTPUT_DIR / f"compare_{output_name}.png"
        v3compare.save_rgba(path, row)
        outputs.append(str(path))
    separator = np.ones((6, rows[0].shape[1], 4), dtype=np.float32) * 0.035
    separator[:, :, 3] = 1.0
    pieces = []
    for index, row in enumerate(rows):
        if index:
            pieces.append(separator)
        pieces.append(row)
    contact = np.concatenate(pieces, axis=0)
    path = cfg.OUTPUT_DIR / "comparison_contact_sheet_v4.png"
    v3compare.save_rgba(path, contact)
    outputs.append(str(path))
    return outputs


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    feature_data = json.loads(cfg.FEATURE_CURVES_PATH.read_text(encoding="utf-8"))
    bpy.ops.wm.open_mainfile(filepath=str(cfg.BLEND_PATH))
    scene = bpy.context.scene
    # world_to_camera_view uses the scene aspect ratio.  The source v3 blend
    # stores a 16:9 default, while every review/annotation image is square.
    scene.render.resolution_x = 512
    scene.render.resolution_y = 512
    scene.render.resolution_percentage = 100
    scene.render.pixel_aspect_x = 1.0
    scene.render.pixel_aspect_y = 1.0
    feature_result = feature_validation(scene, feature_data, args.iteration)
    silhouette_result = silhouette_validation()
    feature_result["silhouette_regression"] = silhouette_result
    feature_result["summary"]["passes_all_required_gates"] = (
        not feature_result["summary"]["failed_metrics"]
        and not feature_result["summary"]["structural_failures"]
        and silhouette_result["pass"]
    )
    cfg.FEATURE_VALIDATION_PATH.write_text(json.dumps(feature_result, indent=2, ensure_ascii=False), encoding="utf-8")
    comparisons = comparison_outputs()
    log = {
        "script": str(Path(__file__).resolve()),
        "iteration": args.iteration,
        "elapsed_seconds": round(time.perf_counter() - started, 4),
        "feature_validation": str(cfg.FEATURE_VALIDATION_PATH),
        "passes_all_required_gates": feature_result["summary"]["passes_all_required_gates"],
        "comparisons": comparisons,
    }
    (cfg.LOG_DIR / "final_compare_v4.json").write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"AEGIS_V4_COMPARE_OK feature={not feature_result['summary']['failed_metrics']} "
        f"silhouette={silhouette_result['pass']} all={feature_result['summary']['passes_all_required_gates']}"
    )


if __name__ == "__main__":
    main()
