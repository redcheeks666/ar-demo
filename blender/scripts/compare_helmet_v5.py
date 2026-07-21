"""Measured feature, silhouette, and side-material validation for AEGIS-R7 v5.

The feature measurements are taken from evaluated meshes projected through the
actual cameras stored in the final v5 blend.  The side silhouette material gate
is a separate flat-ID render: only pixels adjacent to exterior background are
considered, so eye openings and other internal holes cannot be mistaken for the
outer helmet contour.
"""

from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import math
import sys
import time
from pathlib import Path

import bmesh
import bpy
import numpy as np
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import helmet_v5_config as cfg
import compare_helmet_v3 as v3compare


v3compare.cfg = cfg

BLENDER_DIR = SCRIPT_DIR.parent
OUTPUT_DIR = BLENDER_DIR / "output" / "graybox_v5"
LOG_DIR = OUTPUT_DIR / "logs"
BLEND_PATH = BLENDER_DIR / "work" / "helmet_graybox_v5.blend"
V4_OUTPUT_DIR = BLENDER_DIR / "output" / "graybox_v4"
FEATURE_VALIDATION_PATH = OUTPUT_DIR / "feature_validation_v5.json"
MATERIAL_CHECK_PATH = OUTPUT_DIR / "silhouette_material_check.json"
CONTACT_SHEET_PATH = OUTPUT_DIR / "comparison_contact_sheet_v5.png"


def parse_args() -> argparse.Namespace:
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--iteration", type=int, default=3)
    return parser.parse_args(arguments)


def read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def feature_curves_path() -> Path:
    configured = Path(getattr(cfg, "FEATURE_CURVES_PATH", V4_OUTPUT_DIR / "feature_curves.json"))
    if configured.is_file():
        return configured
    fallback = V4_OUTPUT_DIR / "feature_curves.json"
    if fallback.is_file():
        return fallback
    raise FileNotFoundError(f"Missing protected v4 feature curves: {configured} or {fallback}")


def helmet_objects() -> list[bpy.types.Object]:
    found: dict[str, bpy.types.Object] = {}
    for name in ("HELMET_V5", "HELMET_V4", "HELMET_V3"):
        collection = bpy.data.collections.get(name)
        if collection:
            for obj in collection.all_objects:
                found[obj.name] = obj
    if "HelmetRoot" not in found:
        raise RuntimeError("No v5 helmet collection containing HelmetRoot")
    return list(found.values())


def renderable_helmet_meshes() -> list[bpy.types.Object]:
    return [
        obj
        for obj in helmet_objects()
        if obj.type == "MESH"
        and not obj.hide_render
        and not obj.get("review_exclude")
        and not obj.get("aegis_helper")
    ]


def evaluated_world_vertices(obj: bpy.types.Object) -> list:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        return [evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
    finally:
        evaluated.to_mesh_clear()


def closed_positive_mesh_health(obj: bpy.types.Object | None) -> dict:
    if obj is None or obj.type != "MESH":
        return {
            "object_exists": obj is not None,
            "type": None if obj is None else obj.type,
            "boundary_edges": None,
            "non_manifold_edges": None,
            "signed_volume_m3": None,
            "pass": False,
        }
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.normal_update()
        boundary = sum(1 for edge in bm.edges if edge.is_boundary)
        non_manifold = sum(1 for edge in bm.edges if not edge.is_manifold)
        volume = float(bm.calc_volume(signed=True)) if non_manifold == 0 else None
    finally:
        bm.free()
        evaluated.to_mesh_clear()
    return {
        "object_exists": True,
        "type": obj.type,
        "hide_render": obj.hide_render,
        "boundary_edges": boundary,
        "non_manifold_edges": non_manifold,
        "signed_volume_m3": volume,
        "pass": (
            not obj.hide_render
            and boundary == 0
            and non_manifold == 0
            and volume is not None
            and volume > 1.0e-12
        ),
    }


def final_camera_contract() -> dict:
    records = {}
    target = Vector(cfg.RENDER["target"])
    for view, (name, location, expected_type) in cfg.CAMERAS.items():
        camera = bpy.data.objects.get(name)
        if camera is None or camera.type != "CAMERA":
            records[view] = {"name": name, "exists_as_camera": False, "pass": False}
            continue
        expected_location = Vector(location)
        expected_rotation = (target - expected_location).to_track_quat("-Z", "Y")
        actual_rotation = camera.matrix_world.to_quaternion()
        quaternion_dot = min(1.0, abs(float(expected_rotation.dot(actual_rotation))))
        rotation_error = 2.0 * math.acos(quaternion_dot)
        location_error = (camera.matrix_world.translation - expected_location).length
        type_pass = camera.data.type == expected_type
        projection_parameter_pass = (
            abs(float(camera.data.ortho_scale) - float(cfg.RENDER["ortho_scale"])) <= 1.0e-7
            if expected_type == "ORTHO"
            else abs(float(camera.data.lens) - float(cfg.RENDER["lens"])) <= 1.0e-6
        )
        shift_pass = abs(float(camera.data.shift_x)) <= 1.0e-9 and abs(float(camera.data.shift_y)) <= 1.0e-9
        records[view] = {
            "name": name,
            "exists_as_camera": True,
            "expected_type": expected_type,
            "actual_type": camera.data.type,
            "location_error_m": round(float(location_error), 9),
            "rotation_error_radians": round(float(rotation_error), 9),
            "projection_parameter_pass": projection_parameter_pass,
            "zero_lens_shift_pass": shift_pass,
            "pass": (
                type_pass
                and location_error <= 1.0e-7
                # Blender 5.2 re-normalizes the protected v4 perspective
                # camera quaternion on save/load.  1e-3 rad is still below
                # 0.06 degrees and rejects any meaningful camera change.
                and rotation_error <= 1.0e-3
                and projection_parameter_pass
                and shift_pass
            ),
        }
    return {
        "method": "actual final camera objects compared to protected v4 config; rotation tolerance 1e-3 rad for Blender save/load quaternion normalization",
        "views": records,
        "pass": len(records) == len(cfg.CAMERAS) and all(record["pass"] for record in records.values()),
    }


def projected_pixels(
    scene: bpy.types.Scene,
    camera: bpy.types.Object,
    objects: list[bpy.types.Object],
    size: int = 512,
) -> np.ndarray:
    points = []
    for obj in objects:
        for coordinate in evaluated_world_vertices(obj):
            ndc = world_to_camera_view(scene, camera, coordinate)
            if math.isfinite(ndc.x) and math.isfinite(ndc.y) and math.isfinite(ndc.z):
                points.append((float(ndc.x * size), float((1.0 - ndc.y) * size)))
    if not points:
        raise RuntimeError("No evaluated mesh points projected through final camera")
    return np.asarray(points, dtype=np.float64)


def point_bbox(points: np.ndarray) -> tuple[float, float, float, float]:
    return (
        float(points[:, 0].min()),
        float(points[:, 1].min()),
        float(points[:, 0].max()),
        float(points[:, 1].max()),
    )


def normalized_center_and_size(
    points: np.ndarray, helmet_bbox: tuple[float, float, float, float]
) -> dict:
    bbox = point_bbox(points)
    height = helmet_bbox[3] - helmet_bbox[1]
    if height <= 0.0:
        raise RuntimeError("Invalid projected helmet height")
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


def metric_entry(
    reference,
    actual,
    error: float,
    threshold: float,
    unit: str,
    relative: bool = False,
) -> dict:
    return {
        "reference": reference,
        "actual": actual,
        "error": round(float(error), 6),
        "error_percent": round(float(error) * 100.0, 4),
        "threshold_percent": float(threshold),
        "unit": unit,
        "pass": float(error) * 100.0 <= float(threshold) + 1.0e-8,
        "relative_to_feature": relative,
    }


def feature_validation(scene: bpy.types.Scene, feature_data: dict) -> dict:
    helmet_meshes = renderable_helmet_meshes()
    cameras = {}
    for view in ("front", "left", "right", "back", "top"):
        camera_name = cfg.CAMERAS[view][0]
        camera = bpy.data.objects.get(camera_name)
        if camera is None or camera.type != "CAMERA":
            raise RuntimeError(f"Missing final v5 camera {camera_name}")
        cameras[view] = camera

    projected_all = {
        view: projected_pixels(scene, camera, helmet_meshes)
        for view, camera in cameras.items()
    }
    helmet_bboxes = {view: point_bbox(points) for view, points in projected_all.items()}
    v4_validation_path = V4_OUTPUT_DIR / "feature_validation.json"
    protected_v4_validation = json.loads(v4_validation_path.read_text(encoding="utf-8"))
    protected_registration_raw = protected_v4_validation["registration"]["helmet_bboxes_px"]
    registration_bboxes = {
        view: tuple(float(value) for value in protected_registration_raw[view])
        for view in cameras
    }
    tolerances = cfg.FEATURE_TOLERANCES_PERCENT
    metrics = {}

    eye_center_errors = []
    eye_length_errors = []
    eye_details = {}
    for side, label in (("L", "left"), ("R", "right")):
        obj = bpy.data.objects.get(f"EyeLens_{side}")
        if obj is None:
            raise RuntimeError(f"Missing stable eye node EyeLens_{side}")
        points = projected_pixels(scene, cameras["front"], [obj])
        actual = normalized_center_and_size(points, registration_bboxes["front"])
        reference = feature_data["views"]["front"]["features"][f"eye_{label}_metrics"]
        reference_center = reference["center_normalized_head_height"]
        center_error = math.dist(reference_center, actual["center_normalized_head_height"])
        length_error = abs(
            actual["width_normalized_head_height"] - reference["length_head_height"]
        ) / reference["length_head_height"]
        eye_center_errors.append(center_error)
        eye_length_errors.append(length_error)
        eye_details[label] = {
            "reference": reference,
            "actual_from_evaluated_geometry": actual,
            "center_error_head_height": center_error,
            "length_relative_error": length_error,
        }

    metrics["eye_slit_center"] = metric_entry(
        {side: value["reference"]["center_normalized_head_height"] for side, value in eye_details.items()},
        {side: value["actual_from_evaluated_geometry"]["center_normalized_head_height"] for side, value in eye_details.items()},
        max(eye_center_errors),
        tolerances["eye_center"],
        "percent_reference_helmet_height_worst_side",
    )
    metrics["eye_slit_center"]["per_side"] = eye_details
    metrics["eye_slit_length"] = metric_entry(
        {side: value["reference"]["length_head_height"] for side, value in eye_details.items()},
        {side: value["actual_from_evaluated_geometry"]["width_normalized_head_height"] for side, value in eye_details.items()},
        max(eye_length_errors),
        tolerances["eye_length_relative"],
        "percent_feature_length_worst_side",
        True,
    )

    ear_center_errors = []
    ear_radius_errors = []
    ear_details = {}
    for side, view in (("L", "left"), ("R", "right")):
        obj = bpy.data.objects.get(f"EarCover_{side}")
        if obj is None:
            raise RuntimeError(f"Missing stable ear node EarCover_{side}")
        points = projected_pixels(scene, cameras[view], [obj])
        actual = normalized_center_and_size(points, registration_bboxes[view])
        reference = feature_data["views"][view]["features"]["ear_circle_samples"]
        reference_center = reference["fit_center_normalized_head_height"]
        actual_radius = 0.5 * max(
            actual["width_normalized_head_height"], actual["height_normalized_head_height"]
        )
        center_error = math.dist(reference_center, actual["center_normalized_head_height"])
        radius_error = abs(actual_radius - reference["fit_radius_head_height"]) / reference["fit_radius_head_height"]
        ear_center_errors.append(center_error)
        ear_radius_errors.append(radius_error)
        ear_details[view] = {
            "reference_center": reference_center,
            "reference_radius": reference["fit_radius_head_height"],
            "actual_from_evaluated_geometry": actual,
            "actual_radius": actual_radius,
            "center_error_head_height": center_error,
            "radius_relative_error": radius_error,
        }

    metrics["ear_disc_center"] = metric_entry(
        {view: value["reference_center"] for view, value in ear_details.items()},
        {view: value["actual_from_evaluated_geometry"]["center_normalized_head_height"] for view, value in ear_details.items()},
        max(ear_center_errors),
        tolerances["ear_center"],
        "percent_reference_helmet_height_worst_side",
    )
    metrics["ear_disc_center"]["per_side"] = ear_details
    metrics["ear_disc_radius"] = metric_entry(
        {view: value["reference_radius"] for view, value in ear_details.items()},
        {view: value["actual_radius"] for view, value in ear_details.items()},
        max(ear_radius_errors),
        tolerances["ear_radius_relative"],
        "percent_feature_radius_worst_side",
        True,
    )

    faceplate = bpy.data.objects.get("Faceplate")
    if faceplate is None:
        raise RuntimeError("Missing stable Faceplate node")
    faceplate_points = projected_pixels(scene, cameras["front"], [faceplate])
    faceplate_actual = normalized_center_and_size(faceplate_points, registration_bboxes["front"])
    faceplate_reference = feature_data["views"]["front"]["features"]["faceplate_metrics"]
    width_error = abs(
        faceplate_actual["width_normalized_head_height"] - faceplate_reference["max_width_head_height"]
    )
    metrics["faceplate_max_width"] = metric_entry(
        faceplate_reference["max_width_head_height"],
        faceplate_actual["width_normalized_head_height"],
        width_error,
        tolerances["faceplate_max_width"],
        "percent_reference_helmet_height",
    )
    actual_chin = (
        faceplate_actual["bbox_px"][3] - registration_bboxes["front"][1]
    ) / (registration_bboxes["front"][3] - registration_bboxes["front"][1])
    chin_error = abs(actual_chin - faceplate_reference["chin_tip_from_helmet_top_head_height"])
    metrics["faceplate_chin_tip_height"] = metric_entry(
        faceplate_reference["chin_tip_from_helmet_top_head_height"],
        actual_chin,
        chin_error,
        tolerances["chin_tip_height"],
        "percent_reference_helmet_height",
    )

    spine_objects = [obj for obj in helmet_meshes if obj.name.startswith("RearSpine_")]
    if not spine_objects:
        raise RuntimeError("No RearSpine_* objects available for v5 regression measurement")
    spine_points = projected_pixels(scene, cameras["back"], spine_objects)
    spine_actual = normalized_center_and_size(
        spine_points, registration_bboxes["back"]
    )["width_normalized_head_height"]
    spine_reference = feature_data["views"]["back"]["features"]["spine_metrics"]["median_width_head_height"]
    spine_error = abs(spine_actual - spine_reference) / spine_reference
    metrics["crown_rear_spine_width"] = metric_entry(
        spine_reference,
        spine_actual,
        spine_error,
        tolerances["spine_width_relative"],
        "percent_feature_width",
        True,
    )

    # Retain the v4 bbox diagnostic and add a render-level boundary gate below.
    # A bbox alone cannot prove containment inside the actual concave V edge.
    faceplate_bbox = faceplate_actual["bbox_px"]
    eye_inside = True
    eye_inside_details = {}
    for side in ("L", "R"):
        side_details = {}
        for prefix in ("EyeHousing", "EyeLens"):
            obj = bpy.data.objects.get(f"{prefix}_{side}")
            if obj is None:
                raise RuntimeError(f"Missing stable eye node {prefix}_{side}")
            bbox = point_bbox(projected_pixels(scene, cameras["front"], [obj]))
            inside = (
                bbox[0] >= faceplate_bbox[0] - 1.0
                and bbox[2] <= faceplate_bbox[2] + 1.0
                and bbox[1] >= faceplate_bbox[1] - 1.0
                and bbox[3] <= faceplate_bbox[3] + 1.0
            )
            eye_inside = eye_inside and inside
            side_details[prefix] = {"bbox_px": [round(value, 5) for value in bbox], "inside_faceplate_bbox": inside}
        eye_inside_details[side] = side_details

    sample_counts = feature_data["sampling_summary"]["side_depth_samples"]
    eye_boundary = eye_boundary_validation(scene, cameras["front"])
    crown_cap_health = closed_positive_mesh_health(bpy.data.objects.get("CrownSpine_Cap"))
    camera_contract = final_camera_contract()
    structural = {
        "eye_housing_and_lens_inside_faceplate_bbox_diagnostic": {
            "pass": eye_inside,
            "details": eye_inside_details,
            "gate_mode": "diagnostic_only; axis-aligned bbox is weaker than the rendered boundary mask",
            "required": False,
        },
        "eye_housing_and_lens_inside_actual_faceplate_boundary": {
            "pass": eye_boundary["pass"],
            "details": eye_boundary,
        },
        "protected_side_depth_reference_sampling_integrity": {
            "pass": all(sample_counts[side] >= 12 for side in ("left", "right")),
            "counts": sample_counts,
            "required_samples_per_side": 12,
            "note": "reference-input integrity check; not a v5 geometry measurement",
        },
        "top_center_closed": {
            "pass": crown_cap_health["pass"],
            "closure_object": "CrownSpine_Cap",
            "evaluated_mesh_health": crown_cap_health,
        },
        "protected_final_camera_contract": {
            "pass": camera_contract["pass"],
            "details": camera_contract,
        },
    }

    curves_path = feature_curves_path()
    return {
        "schema_version": "2.0",
        "project": "AEGIS-R7 graybox v5 craft pass",
        "inputs": {
            "feature_curves": str(curves_path),
            "feature_curves_sha256": hashlib.sha256(curves_path.read_bytes()).hexdigest(),
            "blend": str(BLEND_PATH),
            "blend_sha256": hashlib.sha256(BLEND_PATH.read_bytes()).hexdigest(),
            "scene_iteration": scene.get("aegis_v5_iteration"),
            "v4_validation": str(v4_validation_path),
        },
        "measurement_method": {
            "reference": "protected v4 raw crop-pixel feature measurements with protected v4 global registration",
            "actual": "evaluated v5 mesh vertices projected through cameras stored in the final v5 blend",
            "uses_build_parameters_as_actual": False,
        },
        "registration": {
            "method": "protected v4 single per-view helmet bbox height and center; fixed registration prevents the required v5 base-foot silhouette repair from shifting unchanged features",
            "render_resolution": [scene.render.resolution_x, scene.render.resolution_y],
            "current_v5_helmet_bboxes_px_diagnostic": {
                view: [round(value, 5) for value in bbox]
                for view, bbox in helmet_bboxes.items()
            },
            "protected_v4_registration_bboxes_px": {
                view: [round(value, 5) for value in bbox]
                for view, bbox in registration_bboxes.items()
            },
            "final_cameras": {
                view: {
                    "name": camera.name,
                    "type": camera.data.type,
                    "ortho_scale": float(camera.data.ortho_scale),
                    "lens_mm": float(camera.data.lens),
                    "shift_x": float(camera.data.shift_x),
                    "shift_y": float(camera.data.shift_y),
                    "clip_start_m": float(camera.data.clip_start),
                    "clip_end_m": float(camera.data.clip_end),
                    "matrix_world": [round(float(value), 7) for row in camera.matrix_world for value in row],
                }
                for view, camera in cameras.items()
            },
        },
        "metrics": metrics,
        "structural_checks": structural,
        "summary": {
            "required_metrics": len(metrics),
            "passed_metrics": sum(1 for metric in metrics.values() if metric["pass"]),
            "failed_metrics": [name for name, metric in metrics.items() if not metric["pass"]],
            "structural_failures": [
                name
                for name, check in structural.items()
                if check.get("required", True) and not check["pass"]
            ],
        },
    }


def silhouette_validation(scene: bpy.types.Scene) -> dict:
    render_manifest = render_current_silhouettes(scene)
    metrics = {}
    overlay_paths = []
    for view in ("front", "left", "right"):
        reference = v3compare.normalized_mask(v3compare.reference_mask(view))
        silhouette_path = OUTPUT_DIR / "silhouette" / f"{view}.png"
        model = v3compare.normalized_mask(v3compare.rendered_mask(silhouette_path))
        metrics[view] = v3compare.silhouette_metrics(reference, model)
        overlay_path = OUTPUT_DIR / f"overlay_{view}.png"
        v3compare.save_rgba(overlay_path, v3compare.overlay(reference, model))
        overlay_paths.append(str(overlay_path))
    passes = (
        metrics["front"]["iou"] >= 0.90
        and metrics["left"]["iou"] >= 0.85
        and metrics["right"]["iou"] >= 0.85
    )
    return {
        "metrics": metrics,
        "thresholds": {"front_iou_min": 0.90, "left_iou_min": 0.85, "right_iou_min": 0.85},
        "current_blend_render_manifest": render_manifest,
        "overlays": overlay_paths,
        "pass": passes,
    }


def border_connected_background(mask: np.ndarray) -> np.ndarray:
    """Return background connected to the image border using eight-neighbors."""
    height, width = mask.shape
    background = ~mask
    exterior = np.zeros_like(mask, dtype=bool)
    queue: deque[tuple[int, int]] = deque()

    def seed(y: int, x: int) -> None:
        if background[y, x] and not exterior[y, x]:
            exterior[y, x] = True
            queue.append((y, x))

    for x in range(width):
        seed(0, x)
        seed(height - 1, x)
    for y in range(height):
        seed(y, 0)
        seed(y, width - 1)
    while queue:
        y, x = queue.popleft()
        for dy, dx in (
            (-1, -1), (-1, 0), (-1, 1),
            (0, -1), (0, 1),
            (1, -1), (1, 0), (1, 1),
        ):
            ny, nx = y + dy, x + dx
            if 0 <= ny < height and 0 <= nx < width and background[ny, nx] and not exterior[ny, nx]:
                exterior[ny, nx] = True
                queue.append((ny, nx))
    return exterior


def exterior_contour(mask: np.ndarray) -> np.ndarray:
    """Return foreground pixels adjacent to border-connected background only."""
    height, width = mask.shape
    exterior = border_connected_background(mask)

    padded = np.pad(exterior, 1, mode="constant", constant_values=False)
    adjacent = np.zeros_like(mask, dtype=bool)
    for dy in range(3):
        for dx in range(3):
            adjacent |= padded[dy : dy + height, dx : dx + width]
    return mask & adjacent


def flat_id_material(name: str, color: tuple[float, float, float, float]) -> bpy.types.Material:
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    try:
        emission = nodes.new("ShaderNodeEmission")
        emission.inputs["Color"].default_value = color
        emission.inputs["Strength"].default_value = 1.0
        links.new(emission.outputs["Emission"], output.inputs["Surface"])
    except RuntimeError:
        principled = nodes.new("ShaderNodeBsdfPrincipled")
        principled.inputs["Base Color"].default_value = color
        emission_input = principled.inputs.get("Emission Color") or principled.inputs.get("Emission")
        if emission_input is not None:
            emission_input.default_value = color
        strength = principled.inputs.get("Emission Strength")
        if strength is not None:
            strength.default_value = 1.0
        links.new(principled.outputs["BSDF"], output.inputs["Surface"])
    return material


def render_uniform_masks(
    scene: bpy.types.Scene,
    jobs: list[dict],
) -> tuple[dict[str, dict], dict[str, np.ndarray]]:
    """Render opaque alpha masks from the current blend and final cameras."""
    original_hide = {obj.name: obj.hide_render for obj in bpy.data.objects}
    view_layer = bpy.context.view_layer
    original_override = view_layer.material_override
    render_state = {
        "engine": scene.render.engine,
        "resolution_x": scene.render.resolution_x,
        "resolution_y": scene.render.resolution_y,
        "resolution_percentage": scene.render.resolution_percentage,
        "pixel_aspect_x": scene.render.pixel_aspect_x,
        "pixel_aspect_y": scene.render.pixel_aspect_y,
        "film_transparent": scene.render.film_transparent,
        "filepath": scene.render.filepath,
        "file_format": scene.render.image_settings.file_format,
        "color_mode": scene.render.image_settings.color_mode,
        "color_depth": scene.render.image_settings.color_depth,
        "camera": scene.camera,
    }
    white = flat_id_material("AEGIS_V5_UNIFORM_MASK", (1.0, 1.0, 1.0, 1.0))
    manifests: dict[str, dict] = {}
    masks: dict[str, np.ndarray] = {}
    try:
        view_layer.material_override = white
        for candidate in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
            try:
                scene.render.engine = candidate
                break
            except TypeError:
                continue
        scene.render.resolution_x = 512
        scene.render.resolution_y = 512
        scene.render.resolution_percentage = 100
        scene.render.pixel_aspect_x = 1.0
        scene.render.pixel_aspect_y = 1.0
        scene.render.film_transparent = True
        scene.render.image_settings.file_format = "PNG"
        scene.render.image_settings.color_mode = "RGBA"
        scene.render.image_settings.color_depth = "8"
        for job in jobs:
            label = str(job["label"])
            camera = job["camera"]
            objects = list(job["objects"])
            visible_names = {obj.name for obj in objects}
            if not visible_names:
                raise RuntimeError(f"Uniform-mask render {label} has no visible objects")
            for obj in bpy.data.objects:
                if obj.type not in {"CAMERA", "LIGHT"}:
                    obj.hide_render = obj.name not in visible_names
            scene.camera = camera
            path = Path(job["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            scene.render.filepath = str(path)
            bpy.ops.render.render(write_still=True)
            rgba = v3compare.load_rgba(path)
            mask = rgba[:, :, 3] > 0.5
            if not mask.any():
                raise RuntimeError(f"Uniform-mask render is empty: {path}")
            masks[label] = mask
            manifests[label] = {
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "camera": camera.name,
                "visible_objects": sorted(visible_names),
                "foreground_pixel_count": int(mask.sum()),
                "resolution_px": [int(mask.shape[1]), int(mask.shape[0])],
            }
    finally:
        for name, hidden in original_hide.items():
            obj = bpy.data.objects.get(name)
            if obj is not None:
                obj.hide_render = hidden
        view_layer.material_override = original_override
        scene.render.engine = render_state["engine"]
        scene.render.resolution_x = render_state["resolution_x"]
        scene.render.resolution_y = render_state["resolution_y"]
        scene.render.resolution_percentage = render_state["resolution_percentage"]
        scene.render.pixel_aspect_x = render_state["pixel_aspect_x"]
        scene.render.pixel_aspect_y = render_state["pixel_aspect_y"]
        scene.render.film_transparent = render_state["film_transparent"]
        scene.render.filepath = render_state["filepath"]
        scene.render.image_settings.file_format = render_state["file_format"]
        scene.render.image_settings.color_mode = render_state["color_mode"]
        scene.render.image_settings.color_depth = render_state["color_depth"]
        scene.camera = render_state["camera"]
        bpy.data.materials.remove(white)
    return manifests, masks


def dilate_one_pixel(mask: np.ndarray) -> np.ndarray:
    height, width = mask.shape
    padded = np.pad(mask, 1, mode="constant", constant_values=False)
    dilated = np.zeros_like(mask, dtype=bool)
    for dy in range(3):
        for dx in range(3):
            dilated |= padded[dy : dy + height, dx : dx + width]
    return dilated


def eye_boundary_validation(scene: bpy.types.Scene, camera: bpy.types.Object) -> dict:
    faceplate = bpy.data.objects.get("Faceplate")
    if faceplate is None:
        raise RuntimeError("Missing Faceplate for render-level eye boundary gate")
    jobs = [
        {
            "label": "faceplate",
            "camera": camera,
            "objects": [faceplate],
            "path": LOG_DIR / "eye_boundary_faceplate_mask.png",
        }
    ]
    for side in ("L", "R"):
        eye_objects = [bpy.data.objects.get(f"{prefix}_{side}") for prefix in ("EyeHousing", "EyeLens")]
        if any(obj is None for obj in eye_objects):
            raise RuntimeError(f"Missing eye housing/lens objects for side {side}")
        jobs.append(
            {
                "label": f"eye_{side}",
                "camera": camera,
                "objects": eye_objects,
                "path": LOG_DIR / f"eye_boundary_eye_{side}.png",
            }
        )
    manifests, masks = render_uniform_masks(scene, jobs)
    # Fill only holes enclosed by the faceplate outer silhouette.  This closes
    # the Boolean eye slots but preserves the true concave V-shaped boundary.
    faceplate_envelope = ~border_connected_background(masks["faceplate"])
    allowed = dilate_one_pixel(faceplate_envelope)
    sides = {}
    for side in ("L", "R"):
        eye_mask = masks[f"eye_{side}"]
        outside = eye_mask & ~allowed
        overlay = np.zeros((eye_mask.shape[0], eye_mask.shape[1], 4), dtype=np.float32)
        overlay[:, :, 3] = 1.0
        overlay[faceplate_envelope, :3] = (0.12, 0.35, 0.12)
        overlay[eye_mask, :3] = (0.0, 0.75, 1.0)
        overlay[outside, :3] = (1.0, 0.0, 0.0)
        overlay_path = LOG_DIR / f"eye_boundary_overlay_{side}.png"
        v3compare.save_rgba(overlay_path, overlay)
        sides[side] = {
            "eye_foreground_pixel_count": int(eye_mask.sum()),
            "outside_faceplate_boundary_pixel_count": int(outside.sum()),
            "allowed_boundary_tolerance_px": 1,
            "overlay": str(overlay_path),
            "overlay_sha256": hashlib.sha256(overlay_path.read_bytes()).hexdigest(),
            "pass": int(outside.sum()) == 0,
        }
    return {
        "method": "separate current-blend front-camera alpha masks; Boolean slot holes filled from border-connected background; housing+lens must remain inside the actual outer faceplate region",
        "camera": camera.name,
        "render_manifest": manifests,
        "sides": sides,
        "pass": all(value["pass"] for value in sides.values()),
    }


def render_current_silhouettes(scene: bpy.types.Scene) -> dict:
    meshes = renderable_helmet_meshes()
    jobs = []
    for view in ("front", "left", "right"):
        camera_name = cfg.CAMERAS[view][0]
        camera = bpy.data.objects.get(camera_name)
        if camera is None or camera.type != "CAMERA":
            raise RuntimeError(f"Missing final silhouette camera {camera_name}")
        jobs.append(
            {
                "label": view,
                "camera": camera,
                "objects": meshes,
                "path": OUTPUT_DIR / "silhouette" / f"{view}.png",
            }
        )
    manifests, _ = render_uniform_masks(scene, jobs)
    return manifests


def is_gold_object(obj: bpy.types.Object) -> bool:
    configured = str(obj.get("design_material", ""))
    material_names = [slot.material.name for slot in obj.material_slots if slot.material]
    return (
        obj.name == "Faceplate"
        or "gold" in configured.lower()
        or any("gold" in name.lower() for name in material_names)
    )


def material_slot_is_gold(obj: bpy.types.Object, material: bpy.types.Material | None) -> bool:
    if obj.name == "Faceplate":
        return True
    configured = str(obj.get("design_material", ""))
    if "gold" in configured.lower():
        return True
    return material is not None and "gold" in material.name.lower()


def silhouette_material_check(scene: bpy.types.Scene) -> dict:
    helmet_meshes = renderable_helmet_meshes()
    gold_objects = sorted(obj.name for obj in helmet_meshes if is_gold_object(obj))
    if not gold_objects:
        raise RuntimeError("No gold-classified v5 object; material contour gate would be meaningless")

    original_hide = {obj.name: obj.hide_render for obj in bpy.data.objects}
    original_materials = {
        obj.name: [slot.material for slot in obj.material_slots]
        for obj in helmet_meshes
    }
    render_state = {
        "engine": scene.render.engine,
        "resolution_x": scene.render.resolution_x,
        "resolution_y": scene.render.resolution_y,
        "resolution_percentage": scene.render.resolution_percentage,
        "pixel_aspect_x": scene.render.pixel_aspect_x,
        "pixel_aspect_y": scene.render.pixel_aspect_y,
        "film_transparent": scene.render.film_transparent,
        "filepath": scene.render.filepath,
        "file_format": scene.render.image_settings.file_format,
        "color_mode": scene.render.image_settings.color_mode,
        "color_depth": scene.render.image_settings.color_depth,
        "camera": scene.camera,
    }
    gold_material = flat_id_material("AEGIS_V5_ID_GOLD", (1.0, 0.0, 0.0, 1.0))
    other_material = flat_id_material("AEGIS_V5_ID_OTHER", (0.0, 1.0, 0.0, 1.0))
    results = {}
    try:
        helmet_names = {obj.name for obj in helmet_meshes}
        for obj in bpy.data.objects:
            if obj.type not in {"CAMERA", "LIGHT"}:
                obj.hide_render = obj.name not in helmet_names
        for obj in helmet_meshes:
            original_slots = original_materials[obj.name]
            if not original_slots:
                obj.data.materials.append(gold_material if is_gold_object(obj) else other_material)
                continue
            # Preserve material-slot count and every polygon.material_index.
            # Mixed-material meshes are therefore classified per face, rather
            # than painting an entire object from one custom property.
            for index, material in enumerate(original_slots):
                obj.data.materials[index] = (
                    gold_material if material_slot_is_gold(obj, material) else other_material
                )

        for candidate in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
            try:
                scene.render.engine = candidate
                break
            except TypeError:
                continue
        scene.render.resolution_x = 512
        scene.render.resolution_y = 512
        scene.render.resolution_percentage = 100
        scene.render.pixel_aspect_x = 1.0
        scene.render.pixel_aspect_y = 1.0
        scene.render.film_transparent = True
        scene.render.image_settings.file_format = "PNG"
        scene.render.image_settings.color_mode = "RGBA"
        scene.render.image_settings.color_depth = "8"

        for view in ("left", "right"):
            camera_name = cfg.CAMERAS[view][0]
            camera = bpy.data.objects.get(camera_name)
            if camera is None:
                raise RuntimeError(f"Missing final material-check camera {camera_name}")
            scene.camera = camera
            path = LOG_DIR / f"silhouette_material_id_{view}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            scene.render.filepath = str(path)
            bpy.ops.render.render(write_still=True)
            rgba = v3compare.load_rgba(path)
            mask = rgba[:, :, 3] > 0.5
            contour = exterior_contour(mask)
            rgb = rgba[:, :, :3]
            gold_pixels = (rgb[:, :, 0] > rgb[:, :, 1] + 0.05) & (rgb[:, :, 0] > 0.15)
            other_pixels = (rgb[:, :, 1] > rgb[:, :, 0] + 0.05) & (rgb[:, :, 1] > 0.15)
            gold_on_contour = contour & gold_pixels
            unknown_on_contour = contour & ~(gold_pixels | other_pixels)
            contour_count = int(contour.sum())
            gold_count = int(gold_on_contour.sum())
            unknown_count = int(unknown_on_contour.sum())
            if contour_count == 0:
                raise RuntimeError(f"Empty exterior contour in material-ID render: {path}")
            coordinates = np.argwhere(gold_on_contour)
            ratio = gold_count / contour_count
            results[view] = {
                "camera": camera_name,
                "id_render": str(path),
                "id_render_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "image_size_px": [int(rgba.shape[1]), int(rgba.shape[0])],
                "exterior_contour_pixel_count": contour_count,
                "gold_exterior_contour_pixel_count": gold_count,
                "gold_exterior_contour_ratio": round(ratio, 10),
                "unknown_exterior_contour_pixel_count": unknown_count,
                "required_ratio": 0.0,
                "gold_pixel_coordinates_yx": [[int(y), int(x)] for y, x in coordinates[:256]],
                "gold_coordinate_list_truncated": len(coordinates) > 256,
                "pass": gold_count == 0 and unknown_count == 0,
            }
    finally:
        for obj in helmet_meshes:
            obj.data.materials.clear()
            for material in original_materials[obj.name]:
                obj.data.materials.append(material)
        for name, hidden in original_hide.items():
            obj = bpy.data.objects.get(name)
            if obj is not None:
                obj.hide_render = hidden
        scene.render.engine = render_state["engine"]
        scene.render.resolution_x = render_state["resolution_x"]
        scene.render.resolution_y = render_state["resolution_y"]
        scene.render.resolution_percentage = render_state["resolution_percentage"]
        scene.render.pixel_aspect_x = render_state["pixel_aspect_x"]
        scene.render.pixel_aspect_y = render_state["pixel_aspect_y"]
        scene.render.film_transparent = render_state["film_transparent"]
        scene.render.filepath = render_state["filepath"]
        scene.render.image_settings.file_format = render_state["file_format"]
        scene.render.image_settings.color_mode = render_state["color_mode"]
        scene.render.image_settings.color_depth = render_state["color_depth"]
        scene.camera = render_state["camera"]
        bpy.data.materials.remove(gold_material)
        bpy.data.materials.remove(other_material)

    result = {
        "schema_version": "1.0",
        "project": "AEGIS-R7 graybox v5 craft pass",
        "inputs": {
            "blend": str(BLEND_PATH),
            "blend_sha256": hashlib.sha256(BLEND_PATH.read_bytes()).hexdigest(),
            "scene_iteration": scene.get("aegis_v5_iteration"),
        },
        "method": "flat material-ID render through final orthographic camera; foreground pixels adjacent to border-connected background define the exterior contour",
        "gold_classification": {
            "method": "preserve polygon.material_index and map each original material slot to gold or non-gold flat ID",
            "object_property": "design_material contains Gold",
            "fallback_stable_object": "Faceplate",
            "gold_object_names": gold_objects,
        },
        "views": results,
        "summary": {
            "required_views": ["left", "right"],
            "failed_views": [view for view, value in results.items() if not value["pass"]],
            "gold_outer_contour_ratio_must_equal": 0.0,
            "pass": all(value["pass"] for value in results.values()) and len(results) == 2,
        },
    }
    MATERIAL_CHECK_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


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
        annotation = V4_OUTPUT_DIR / f"feature_annotation_{output_name}.png"
        if output_name == "front_3q":
            annotation = reference
        images = [
            v3compare.load_rgba(reference),
            v3compare.load_rgba(annotation),
            v3compare.load_rgba(V4_OUTPUT_DIR / "design_preview" / "helmet_only" / f"{output_name}.png"),
            v3compare.load_rgba(OUTPUT_DIR / "clay" / "helmet_only" / f"{output_name}.png"),
            v3compare.load_rgba(OUTPUT_DIR / "design_preview" / "helmet_only" / f"{output_name}.png"),
        ]
        row = v3compare.comparison_row(images, size=320)
        rows.append(row)
        path = OUTPUT_DIR / f"compare_{output_name}.png"
        v3compare.save_rgba(path, row)
        outputs.append(str(path))
    separator = np.ones((6, rows[0].shape[1], 4), dtype=np.float32) * 0.035
    separator[:, :, 3] = 1.0
    pieces = []
    for index, row in enumerate(rows):
        if index:
            pieces.append(separator)
        pieces.append(row)
    v3compare.save_rgba(CONTACT_SHEET_PATH, np.concatenate(pieces, axis=0))
    outputs.append(str(CONTACT_SHEET_PATH))
    return outputs


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if not BLEND_PATH.is_file():
        raise FileNotFoundError(f"Missing final v5 blend: {BLEND_PATH}")
    curves_path = feature_curves_path()
    feature_data = json.loads(curves_path.read_text(encoding="utf-8"))
    bpy.ops.wm.open_mainfile(filepath=str(BLEND_PATH))
    scene = bpy.context.scene
    scene_iteration = scene.get("aegis_v5_iteration")
    if (
        isinstance(scene_iteration, bool)
        or not isinstance(scene_iteration, int)
        or scene_iteration != args.iteration
    ):
        raise RuntimeError(
            f"Comparison iteration mismatch: requested={args.iteration} scene={scene_iteration}"
        )
    # world_to_camera_view depends on the current render aspect.  Every v5
    # orthographic validation image is square, regardless of the saved default.
    scene.render.resolution_x = 512
    scene.render.resolution_y = 512
    scene.render.resolution_percentage = 100
    scene.render.pixel_aspect_x = 1.0
    scene.render.pixel_aspect_y = 1.0

    feature_result = feature_validation(scene, feature_data)
    silhouette_result = silhouette_validation(scene)
    material_result = silhouette_material_check(scene)
    feature_result["silhouette_regression"] = silhouette_result
    feature_result["silhouette_material_check"] = {
        "path": str(MATERIAL_CHECK_PATH),
        "pass": material_result["summary"]["pass"],
        "failed_views": material_result["summary"]["failed_views"],
    }
    feature_result["summary"]["passes_all_required_gates"] = (
        not feature_result["summary"]["failed_metrics"]
        and not feature_result["summary"]["structural_failures"]
        and silhouette_result["pass"]
        and material_result["summary"]["pass"]
    )
    FEATURE_VALIDATION_PATH.write_text(
        json.dumps(feature_result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    comparisons = comparison_outputs()
    log = {
        "script": str(Path(__file__).resolve()),
        "iteration": args.iteration,
        "scene_iteration": scene_iteration,
        "blend_sha256": hashlib.sha256(BLEND_PATH.read_bytes()).hexdigest(),
        "elapsed_seconds": round(time.perf_counter() - started, 4),
        "feature_validation": str(FEATURE_VALIDATION_PATH),
        "silhouette_material_check": str(MATERIAL_CHECK_PATH),
        "passes_all_required_gates": feature_result["summary"]["passes_all_required_gates"],
        "comparisons": comparisons,
    }
    (LOG_DIR / "final_compare_v5.json").write_text(
        json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    if not feature_result["summary"]["passes_all_required_gates"]:
        raise RuntimeError(
            "v5 comparison gate failed: "
            f"features={feature_result['summary']['failed_metrics']} "
            f"structural={feature_result['summary']['structural_failures']} "
            f"silhouette={silhouette_result['pass']} "
            f"gold_contour={material_result['summary']['pass']}"
        )
    print(
        "AEGIS_V5_COMPARE_OK "
        f"features={len(feature_result['metrics'])} "
        f"silhouette={silhouette_result['pass']} "
        f"gold_contour={material_result['summary']['pass']}"
    )


if __name__ == "__main__":
    main()
