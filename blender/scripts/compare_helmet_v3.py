"""Compare AEGIS-R7 v3 renders against reference silhouettes and v2 images."""

from __future__ import annotations

import argparse
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

import helmet_v3_config as cfg


def parse_args() -> argparse.Namespace:
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--iteration", type=int, default=1)
    parser.add_argument("--final", action="store_true")
    return parser.parse_args(arguments)


def load_rgba(path: Path) -> np.ndarray:
    if not path.is_file():
        raise FileNotFoundError(path)
    image = bpy.data.images.load(str(path), check_existing=False)
    image.colorspace_settings.name = "Non-Color"
    width, height = image.size
    pixels = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    bpy.data.images.remove(image)
    return np.flipud(pixels.reshape(height, width, 4))


def save_rgba(path: Path, rgba: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    height, width = rgba.shape[:2]
    image = bpy.data.images.new(f"AEGIS_V3_COMPARE_{path.stem}", width=width, height=height, alpha=True)
    image.colorspace_settings.name = "Non-Color"
    image.pixels.foreach_set(np.flipud(np.clip(rgba, 0.0, 1.0)).astype(np.float32).reshape(-1))
    image.filepath_raw = str(path)
    image.file_format = "PNG"
    image.save()
    bpy.data.images.remove(image)


def bbox(mask: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        raise RuntimeError("Empty silhouette mask")
    return int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1)


def resize_nearest(array: np.ndarray, height: int, width: int) -> np.ndarray:
    source_height, source_width = array.shape[:2]
    y_indices = np.minimum(source_height - 1, (np.arange(height) * source_height / height).astype(int))
    x_indices = np.minimum(source_width - 1, (np.arange(width) * source_width / width).astype(int))
    return array[y_indices[:, None], x_indices[None, :]]


def normalized_mask(mask: np.ndarray, size: int = 512, padding: int = 22) -> np.ndarray:
    x0, y0, x1, y1 = bbox(mask)
    cropped = mask[y0:y1, x0:x1]
    target_height = size - padding * 2
    target_width = max(1, int(round(cropped.shape[1] * target_height / cropped.shape[0])))
    if target_width > size - padding * 2:
        target_width = size - padding * 2
        target_height = max(1, int(round(cropped.shape[0] * target_width / cropped.shape[1])))
    resized = resize_nearest(cropped, target_height, target_width)
    canvas = np.zeros((size, size), dtype=bool)
    offset_y = (size - target_height) // 2
    offset_x = (size - target_width) // 2
    canvas[offset_y : offset_y + target_height, offset_x : offset_x + target_width] = resized
    return canvas


def reference_mask(view: str) -> np.ndarray:
    image = load_rgba(cfg.DERIVED_REFERENCE_DIR / f"{view}_mask.png")
    return image[:, :, 0] > 0.5


def rendered_mask(path: Path) -> np.ndarray:
    image = load_rgba(path)
    alpha = image[:, :, 3]
    if float(alpha.max()) > 0.5 and float(alpha.min()) < 0.5:
        return alpha > 0.12
    luminance = image[:, :, :3].mean(axis=2)
    return luminance < 0.35


def erode(mask: np.ndarray) -> np.ndarray:
    padded = np.pad(mask, 1, mode="constant", constant_values=False)
    result = np.ones_like(mask)
    for y in range(3):
        for x in range(3):
            result &= padded[y : y + mask.shape[0], x : x + mask.shape[1]]
    return result


def contour(mask: np.ndarray) -> np.ndarray:
    return mask & ~erode(mask)


def nearest_distances(source_points: np.ndarray, target_points: np.ndarray) -> np.ndarray:
    distances = np.empty(len(source_points), dtype=np.float32)
    chunk_size = 256
    for start in range(0, len(source_points), chunk_size):
        chunk = source_points[start : start + chunk_size].astype(np.float32)
        delta = chunk[:, None, :] - target_points[None, :, :].astype(np.float32)
        squared = np.sum(delta * delta, axis=2)
        distances[start : start + len(chunk)] = np.sqrt(squared.min(axis=1))
    return distances


def silhouette_metrics(reference: np.ndarray, model: np.ndarray) -> dict:
    intersection = int(np.logical_and(reference, model).sum())
    union = int(np.logical_or(reference, model).sum())
    iou = intersection / union if union else 0.0
    reference_points = np.argwhere(contour(reference))
    model_points = np.argwhere(contour(model))
    model_to_reference = nearest_distances(model_points, reference_points)
    reference_to_model = nearest_distances(reference_points, model_points)
    mean_distance = 0.5 * (float(model_to_reference.mean()) + float(reference_to_model.mean()))
    reference_height = bbox(reference)[3] - bbox(reference)[1]
    maximum_index = int(np.argmax(model_to_reference))
    maximum_location = model_points[maximum_index]
    return {
        "iou": round(iou, 6),
        "mean_contour_distance_px": round(mean_distance, 4),
        "mean_contour_distance_percent_height": round(100.0 * mean_distance / reference_height, 4),
        "maximum_contour_error_px": round(float(model_to_reference[maximum_index]), 4),
        "maximum_error_location_yx": [int(maximum_location[0]), int(maximum_location[1])],
        "reference_area_px": int(reference.sum()),
        "model_area_px": int(model.sum()),
    }


def overlay(reference: np.ndarray, model: np.ndarray) -> np.ndarray:
    rgba = np.zeros((reference.shape[0], reference.shape[1], 4), dtype=np.float32)
    only_reference = reference & ~model
    only_model = model & ~reference
    both = reference & model
    rgba[only_reference] = (0.95, 0.12, 0.10, 1.0)
    rgba[only_model] = (0.05, 0.78, 0.95, 1.0)
    rgba[both] = (0.92, 0.92, 0.92, 1.0)
    rgba[~(reference | model)] = (0.055, 0.065, 0.080, 1.0)
    return rgba


def fit_square(image: np.ndarray, size: int = 384) -> np.ndarray:
    height, width = image.shape[:2]
    scale = min(size / width, size / height)
    target_width = max(1, int(round(width * scale)))
    target_height = max(1, int(round(height * scale)))
    resized = resize_nearest(image, target_height, target_width)
    canvas = np.empty((size, size, 4), dtype=np.float32)
    canvas[:, :, :3] = (0.16, 0.18, 0.21)
    canvas[:, :, 3] = 1.0
    offset_x = (size - target_width) // 2
    offset_y = (size - target_height) // 2
    canvas[offset_y : offset_y + target_height, offset_x : offset_x + target_width] = resized
    return canvas


def comparison_row(images: list[np.ndarray], size: int = 384) -> np.ndarray:
    panels = [fit_square(image, size) for image in images]
    separator = np.ones((size, 4, 4), dtype=np.float32) * 0.04
    separator[:, :, 3] = 1.0
    pieces = []
    for index, panel in enumerate(panels):
        if index:
            pieces.append(separator)
        pieces.append(panel)
    return np.concatenate(pieces, axis=1)


def build_comparisons(overlays: dict[str, np.ndarray], iteration: int) -> list[str]:
    historical_dir = cfg.BLENDER_DIR / "output" / "graybox" / "helmet_only"
    clay_dir = cfg.OUTPUT_DIR / "clay" / "helmet_only"
    design_dir = cfg.OUTPUT_DIR / "design_preview" / "helmet_only"
    iteration_dir = cfg.OUTPUT_DIR / "silhouette_iterations" / f"iteration_{iteration}"
    rows = {}
    definitions = {
        "front": (
            cfg.DERIVED_REFERENCE_DIR / "front_crop.png",
            historical_dir / "front.png",
            clay_dir / "front.png",
            design_dir / "front.png",
            overlays["front"],
        ),
        "side": (
            cfg.DERIVED_REFERENCE_DIR / "right_crop.png",
            historical_dir / "right.png",
            clay_dir / "right.png",
            design_dir / "right.png",
            overlays["right"],
        ),
        "front_3q": (
            cfg.DERIVED_REFERENCE_DIR / "closed_front_3q_crop.png",
            historical_dir / "front_3q.png",
            clay_dir / "front_3q.png",
            design_dir / "front_3q.png",
            overlay(
                normalized_mask(reference_mask("closed_front_3q")),
                normalized_mask(rendered_mask(iteration_dir / "front_3q.png")),
            ),
        ),
    }
    outputs = []
    for name, definition in definitions.items():
        images = [load_rgba(path) if isinstance(path, Path) else path for path in definition]
        rows[name] = comparison_row(images)
        path = cfg.OUTPUT_DIR / f"comparison_{name}.png"
        save_rgba(path, rows[name])
        outputs.append(str(path))
    separator = np.ones((5, rows["front"].shape[1], 4), dtype=np.float32) * 0.04
    separator[:, :, 3] = 1.0
    contact_sheet = np.concatenate(
        (rows["front"], separator, rows["side"], separator, rows["front_3q"]), axis=0
    )
    contact_path = cfg.OUTPUT_DIR / "comparison_contact_sheet.png"
    save_rgba(contact_path, contact_sheet)
    outputs.append(str(contact_path))
    return outputs


def main() -> None:
    args = parse_args()
    started = time.perf_counter()
    iteration_dir = cfg.OUTPUT_DIR / "silhouette_iterations" / f"iteration_{args.iteration}"
    final_silhouette_dir = cfg.OUTPUT_DIR / "silhouette"
    metrics = {}
    overlays = {}
    output_paths = []
    for view in ("front", "right", "left"):
        reference = normalized_mask(reference_mask(view))
        render_path = (
            final_silhouette_dir / f"{view}.png"
            if args.final
            else iteration_dir / f"{view}.png"
        )
        model = normalized_mask(rendered_mask(render_path))
        metrics[view] = silhouette_metrics(reference, model)
        overlay_image = overlay(reference, model)
        overlays[view] = overlay_image
        output_path = (
            cfg.OUTPUT_DIR / f"overlay_{view}.png"
            if args.final
            else iteration_dir / f"overlay_{view}.png"
        )
        save_rgba(output_path, overlay_image)
        output_paths.append(str(output_path))

    contact_outputs = build_comparisons(overlays, args.iteration) if args.final else []
    output_paths.extend(contact_outputs)
    elapsed = time.perf_counter() - started
    result = {
        "script": str(Path(__file__).resolve()),
        "iteration": args.iteration,
        "final": args.final,
        "elapsed_seconds": round(elapsed, 4),
        "metrics": metrics,
        "thresholds": {
            "front_iou_min": 0.82,
            "side_iou_min": 0.78,
            "front_mean_error_percent_max": 3.5,
            "side_mean_error_percent_max": 5.0,
        },
        "outputs": output_paths,
    }
    result["passes_numeric_gate"] = (
        metrics["front"]["iou"] >= 0.82
        and min(metrics["right"]["iou"], metrics["left"]["iou"]) >= 0.78
        and metrics["front"]["mean_contour_distance_percent_height"] <= 3.5
        and max(
            metrics["right"]["mean_contour_distance_percent_height"],
            metrics["left"]["mean_contour_distance_percent_height"],
        )
        <= 5.0
    )
    log_name = "silhouette_metrics_final.json" if args.final else f"iteration_{args.iteration}_compare.json"
    (cfg.LOG_DIR / log_name).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"AEGIS_V3_COMPARE_OK iteration={args.iteration} final={args.final} "
        f"front_iou={metrics['front']['iou']:.4f} "
        f"side_min_iou={min(metrics['right']['iou'], metrics['left']['iou']):.4f} "
        f"gate={result['passes_numeric_gate']}"
    )


if __name__ == "__main__":
    main()
