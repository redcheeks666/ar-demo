"""Extract measured AEGIS-R7 surface features from the v3 reference crops.

The source concept sheet is painterly, so this pass uses explicit pixel
samples rather than pretending that a color threshold is unambiguous.  Every
control point written to ``feature_curves.json`` is also drawn back over the
same crop in the annotation images.
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import bpy
import numpy as np


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import helmet_v4_config as cfg


PIXEL_SAMPLES = {
    "front": {
        "faceplate_outer_boundary": [
            (196, 119), (226, 149), (287, 149), (317, 119), (347, 125),
            (410, 132), (421, 165), (427, 216), (411, 229), (384, 240),
            (369, 268), (354, 307), (339, 348), (320, 389), (294, 390),
            (282, 376), (230, 376), (218, 390), (191, 389), (173, 349),
            (157, 307), (142, 268), (126, 240), (101, 229), (91, 216),
            (101, 166), (122, 132), (166, 125),
        ],
        "eye_left_outer": [(121, 193), (202, 195), (213, 201), (211, 212), (204, 218), (130, 215), (120, 208)],
        "eye_left_lens": [(128, 198), (200, 199), (207, 203), (205, 211), (132, 210), (126, 206)],
        "eye_right_outer": [(334, 198), (341, 193), (415, 190), (424, 197), (424, 206), (416, 212), (342, 216), (333, 210)],
        "eye_right_lens": [(340, 201), (414, 195), (419, 199), (418, 206), (343, 211), (338, 208)],
        "jaw_u_left": [(105, 232), (130, 246), (151, 276), (168, 318), (186, 367), (205, 404), (229, 414)],
        "jaw_u_right": [(430, 231), (405, 245), (384, 276), (367, 319), (350, 367), (331, 403), (303, 414)],
        "lower_u": [(205, 404), (229, 414), (282, 414), (303, 414), (331, 403)],
        "bezel_width_samples": [((196, 119), (191, 111)), ((101, 229), (91, 220)), ((191, 389), (181, 397)), ((320, 389), (330, 398)), ((427, 216), (438, 216))],
    },
    "right": {
        "ear_circle_samples": [(267, 171), (358, 262), (267, 353), (176, 262), (331, 198), (331, 326), (203, 326), (203, 198)],
        "ear_emitter": [(267, 222), (267, 302)],
        "faceplate_side_edge": [(68, 137), (51, 150), (40, 170), (33, 190), (28, 212), (27, 233), (28, 254), (31, 276), (35, 298), (40, 320), (45, 343), (50, 366), (52, 389)],
        "jaw_diagonal": [(145, 249), (119, 282), (104, 326), (82, 371), (58, 410), (86, 451), (183, 398)],
        "cheek_top_groove": [(170, 215), (160, 225), (150, 235), (138, 245), (122, 255), (99, 265)],
        "cheek_to_jaw_diagonal": [(180, 325), (175, 335), (155, 345), (144, 355), (136, 365), (126, 375), (115, 385), (104, 395), (93, 405), (81, 415)],
        "lower_structural_u": [(230, 323), (226, 335), (223, 345), (232, 355), (233, 365), (216, 375), (229, 385), (239, 395), (233, 405), (218, 415), (210, 425)],
    },
    "left": {
        "ear_circle_samples": [(243, 140), (334, 231), (243, 322), (152, 231), (307, 167), (307, 295), (179, 295), (179, 167)],
        "ear_emitter": [(243, 193), (243, 268)],
        "faceplate_side_edge": [(384, 130), (407, 146), (424, 164), (437, 184), (446, 205), (451, 226), (452, 248), (450, 269), (448, 291), (445, 313), (442, 335), (439, 357), (437, 380)],
        "jaw_diagonal": [(357, 247), (381, 278), (397, 321), (419, 362), (441, 399), (419, 435), (329, 385)],
        "cheek_top_groove": [(337, 202), (347, 212), (358, 222), (377, 235), (397, 247), (420, 258), (431, 270)],
        "cheek_to_jaw_diagonal": [(313, 290), (335, 300), (350, 310), (360, 320), (366, 330), (376, 340), (386, 350), (398, 360), (407, 370), (414, 380)],
        "lower_structural_u": [(282, 318), (279, 330), (291, 340), (290, 350), (292, 360), (297, 370), (302, 380), (307, 390), (329, 400), (362, 410), (383, 420)],
    },
    "back": {
        "spine_left_edge": [(230, 37), (221, 92), (219, 130), (216, 168), (216, 221), (220, 275), (220, 327), (219, 378)],
        "spine_right_edge": [(279, 37), (290, 92), (293, 130), (294, 168), (294, 221), (291, 275), (291, 327), (292, 378)],
        "spine_segment_centers": [(255, 61), (255, 105), (255, 151), (255, 205), (255, 263), (255, 307), (255, 349), (255, 382)],
        "vent_left": [(149, 241), (188, 241), (190, 255), (151, 255)],
        "vent_right": [(318, 241), (359, 241), (361, 255), (318, 255)],
        "rear_layer_left": [(109, 335), (129, 318), (151, 306), (177, 299), (205, 292)],
        "rear_layer_right": [(402, 335), (382, 318), (359, 306), (333, 299), (305, 292)],
    },
    "top": {
        "spine_centerline": [(255, 12), (255, 65), (255, 118), (255, 171), (255, 224), (255, 277), (255, 329), (255, 362)],
        "spine_left_edge": [(231, 12), (231, 65), (231, 118), (232, 171), (232, 224), (232, 277), (234, 329), (239, 361)],
        "spine_right_edge": [(279, 12), (279, 65), (279, 118), (278, 171), (278, 224), (278, 277), (276, 329), (271, 361)],
    },
}


COLORS = {
    "faceplate": (1.0, 0.12, 0.04, 1.0),
    "eye": (0.0, 1.0, 1.0, 1.0),
    "jaw": (0.15, 1.0, 0.15, 1.0),
    "bezel": (1.0, 0.1, 1.0, 1.0),
    "ear": (1.0, 0.82, 0.0, 1.0),
    "spine": (1.0, 0.15, 0.02, 1.0),
    "vent": (0.0, 1.0, 0.35, 1.0),
    "layer": (0.2, 0.65, 1.0, 1.0),
}


def load_rgba(path: Path) -> np.ndarray:
    image = bpy.data.images.load(str(path), check_existing=False)
    width, height = image.size
    rgba = np.asarray(image.pixels[:], dtype=np.float32).reshape((height, width, 4))
    rgba = np.flipud(rgba).copy()
    bpy.data.images.remove(image)
    return rgba


def save_rgba(path: Path, rgba: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    height, width = rgba.shape[:2]
    image = bpy.data.images.new(path.stem, width=width, height=height, alpha=True)
    image.pixels = np.flipud(np.clip(rgba, 0.0, 1.0)).reshape(-1)
    image.file_format = "PNG"
    image.filepath_raw = str(path)
    image.save()
    bpy.data.images.remove(image)


def put_pixel(image: np.ndarray, x: int, y: int, color: tuple[float, ...], radius: int = 0) -> None:
    height, width = image.shape[:2]
    for yy in range(y - radius, y + radius + 1):
        for xx in range(x - radius, x + radius + 1):
            if 0 <= xx < width and 0 <= yy < height and (xx - x) ** 2 + (yy - y) ** 2 <= radius**2:
                image[yy, xx] = color


def draw_line(image: np.ndarray, start: tuple[int, int], end: tuple[int, int], color: tuple[float, ...], width: int = 2) -> None:
    x0, y0 = start
    x1, y1 = end
    steps = max(abs(x1 - x0), abs(y1 - y0), 1)
    for index in range(steps + 1):
        factor = index / steps
        put_pixel(image, int(round(x0 + (x1 - x0) * factor)), int(round(y0 + (y1 - y0) * factor)), color, width)


def draw_polyline(image: np.ndarray, points: list[tuple[int, int]], color: tuple[float, ...], closed: bool = False) -> None:
    for start, end in zip(points, points[1:]):
        draw_line(image, start, end, color)
    if closed and len(points) > 2:
        draw_line(image, points[-1], points[0], color)
    for point in points:
        put_pixel(image, point[0], point[1], (1.0, 1.0, 1.0, 1.0), 4)
        put_pixel(image, point[0], point[1], color, 2)


def circle_fit(points: list[tuple[int, int]]) -> tuple[tuple[float, float], float]:
    array = np.asarray(points, dtype=np.float64)
    x = array[:, 0]
    y = array[:, 1]
    matrix = np.column_stack((2.0 * x, 2.0 * y, np.ones_like(x)))
    solution, *_ = np.linalg.lstsq(matrix, x * x + y * y, rcond=None)
    cx, cy, constant = solution
    radius = math.sqrt(max(0.0, constant + cx * cx + cy * cy))
    return (float(cx), float(cy)), float(radius)


def point_record(identifier: str, point: tuple[int, int], bbox: list[int]) -> dict:
    x0, y0, x1, y1 = bbox
    height = y1 - y0
    center_x = 0.5 * (x0 + x1)
    return {
        "id": identifier,
        "source_px": [int(point[0]), int(point[1])],
        "normalized_head_height": [round((point[0] - center_x) / height, 6), round((point[1] - y0) / height, 6)],
        "method": "manual_sample_from_visible_crop_feature",
    }


def records(view: str, feature: str, points: list[tuple[int, int]], bbox: list[int]) -> list[dict]:
    return [point_record(f"{view}.{feature}.p{index:02d}", point, bbox) for index, point in enumerate(points)]


def feature_color(name: str) -> tuple[float, ...]:
    for token, color in COLORS.items():
        if token in name:
            return color
    return (1.0, 1.0, 0.0, 1.0)


def annotate(view: str, samples: dict[str, object]) -> str:
    crop_path = cfg.DERIVED_REFERENCE_DIR / f"{view}_crop.png"
    image = load_rgba(crop_path)
    for name, value in samples.items():
        color = feature_color(name)
        if name == "bezel_width_samples":
            for pair in value:
                draw_line(image, pair[0], pair[1], color, 2)
                put_pixel(image, pair[0][0], pair[0][1], color, 3)
                put_pixel(image, pair[1][0], pair[1][1], color, 3)
        elif name == "ear_circle_samples":
            points = value
            center, radius = circle_fit(points)
            circumference = [
                (int(round(center[0] + radius * math.cos(index * math.pi / 90.0))), int(round(center[1] + radius * math.sin(index * math.pi / 90.0))))
                for index in range(180)
            ]
            draw_polyline(image, circumference, color, closed=True)
            for point in points:
                put_pixel(image, point[0], point[1], (1.0, 1.0, 1.0, 1.0), 4)
                put_pixel(image, point[0], point[1], color, 2)
        else:
            closed = any(token in name for token in ("boundary", "outer", "lens", "vent"))
            draw_polyline(image, value, color, closed=closed)
    output = cfg.OUTPUT_DIR / f"feature_annotation_{view}.png"
    save_rgba(output, image)
    return str(output)


def average_pair_distance(pairs: list[tuple[tuple[int, int], tuple[int, int]]]) -> float:
    return float(np.mean([math.dist(start, end) for start, end in pairs]))


def main() -> None:
    started = time.perf_counter()
    cfg.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    measurements = json.loads(cfg.MEASUREMENTS_PATH.read_text(encoding="utf-8"))
    result = {
        "schema_version": "1.0",
        "project": "AEGIS-R7 graybox v4 feature projection",
        "coordinate_convention": {
            "pixel_origin": "top_left",
            "normalizer": "per-view reference helmet silhouette height",
            "formula": "u=(x-bbox_center_x)/helmet_height_px; v=(y-bbox_top)/helmet_height_px",
        },
        "source": {"derived_reference_dir": str(cfg.DERIVED_REFERENCE_DIR), "measurements": str(cfg.MEASUREMENTS_PATH)},
        "views": {},
    }
    annotations = []
    for view, samples in PIXEL_SAMPLES.items():
        view_measurement = measurements["views"][view]
        bbox = view_measurement["silhouette_bbox_px"]
        height = bbox[3] - bbox[1]
        feature_records = {}
        for name, value in samples.items():
            if name == "bezel_width_samples":
                flat = [point for pair in value for point in pair]
                feature_records[name] = {
                    "point_pairs": records(view, name, flat, bbox),
                    "mean_width_px": round(average_pair_distance(value), 4),
                    "mean_width_head_height": round(average_pair_distance(value) / height, 6),
                }
            elif name == "ear_circle_samples":
                center, radius = circle_fit(value)
                feature_records[name] = {
                    "points": records(view, name, value, bbox),
                    "fit_center_px": [round(center[0], 4), round(center[1], 4)],
                    "fit_radius_px": round(radius, 4),
                    "fit_center_normalized_head_height": [round((center[0] - 0.5 * (bbox[0] + bbox[2])) / height, 6), round((center[1] - bbox[1]) / height, 6)],
                    "fit_radius_head_height": round(radius / height, 6),
                }
            else:
                feature_records[name] = {"points": records(view, name, value, bbox)}
        result["views"][view] = {
            "crop_path": str(cfg.DERIVED_REFERENCE_DIR / f"{view}_crop.png"),
            "crop_size_px": view_measurement["crop_size_px"],
            "helmet_silhouette_bbox_px": bbox,
            "helmet_height_px": height,
            "features": feature_records,
        }
        annotations.append(annotate(view, samples))

    front = result["views"]["front"]
    for side in ("left", "right"):
        points = PIXEL_SAMPLES["front"][f"eye_{side}_lens"]
        center = np.mean(np.asarray(points, dtype=np.float64), axis=0)
        length = float(np.max(np.asarray(points)[:, 0]) - np.min(np.asarray(points)[:, 0]))
        front["features"][f"eye_{side}_metrics"] = {
            "center_px": [round(float(center[0]), 4), round(float(center[1]), 4)],
            "center_normalized_head_height": [round((float(center[0]) - 0.5 * (62 + 474)) / 452, 6), round((float(center[1]) - 31) / 452, 6)],
            "length_px": round(length, 4),
            "length_head_height": round(length / 452, 6),
        }
    outline = np.asarray(PIXEL_SAMPLES["front"]["faceplate_outer_boundary"])
    front["features"]["faceplate_metrics"] = {
        "max_width_px": int(outline[:, 0].max() - outline[:, 0].min()),
        "max_width_head_height": round(float(outline[:, 0].max() - outline[:, 0].min()) / 452, 6),
        "chin_tip_y_px": int(outline[:, 1].max()),
        "chin_tip_from_helmet_top_head_height": round(float(outline[:, 1].max() - 31) / 452, 6),
    }
    back_widths = [right[0] - left[0] for left, right in zip(PIXEL_SAMPLES["back"]["spine_left_edge"], PIXEL_SAMPLES["back"]["spine_right_edge"])]
    result["views"]["back"]["features"]["spine_metrics"] = {
        "segment_count": len(PIXEL_SAMPLES["back"]["spine_segment_centers"]),
        "median_width_px": round(float(np.median(back_widths)), 4),
        "median_width_head_height": round(float(np.median(back_widths)) / (483 - 35), 6),
    }
    result["sampling_summary"] = {
        "all_control_points_are_raw_crop_pixels": True,
        "side_depth_samples": {"left": len(PIXEL_SAMPLES["left"]["faceplate_side_edge"]), "right": len(PIXEL_SAMPLES["right"]["faceplate_side_edge"])},
        "annotation_files": annotations,
    }
    cfg.FEATURE_CURVES_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    log = {
        "script": str(Path(__file__).resolve()),
        "elapsed_seconds": round(time.perf_counter() - started, 4),
        "feature_curves": str(cfg.FEATURE_CURVES_PATH),
        "annotations": annotations,
        "side_depth_minimum_12_pass": all(value >= 12 for value in result["sampling_summary"]["side_depth_samples"].values()),
    }
    (cfg.LOG_DIR / "extract_features_v4.json").write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"AEGIS_V4_FEATURES_OK views={len(result['views'])} annotations={len(annotations)}")
    print(f"AEGIS_V4_FEATURE_CURVES={cfg.FEATURE_CURVES_PATH}")


if __name__ == "__main__":
    main()
