"""Crop, segment, measure, and persist AEGIS-R7 v3 reference data.

Run with Blender so no external image-processing package is required.
"""

from __future__ import annotations

import json
import sys
import time
from collections import deque
from pathlib import Path

import bpy
import numpy as np


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import helmet_v3_config as cfg


def load_rgba_top_origin(path: Path) -> np.ndarray:
    image = bpy.data.images.load(str(path), check_existing=False)
    image.colorspace_settings.name = "Non-Color"
    width, height = image.size
    pixels = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    bpy.data.images.remove(image)
    return np.flipud(pixels.reshape(height, width, 4))


def save_rgba_top_origin(path: Path, rgba: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    height, width = rgba.shape[:2]
    pixels = np.flipud(np.clip(rgba, 0.0, 1.0)).astype(np.float32)
    image = bpy.data.images.new(
        name=f"AEGIS_V3_{path.stem}",
        width=width,
        height=height,
        alpha=True,
        float_buffer=False,
    )
    image.colorspace_settings.name = "Non-Color"
    image.pixels.foreach_set(pixels.reshape(-1))
    image.filepath_raw = str(path)
    image.file_format = "PNG"
    image.save()
    bpy.data.images.remove(image)


def crop_image(source: np.ndarray, rectangle: tuple[int, int, int, int]) -> np.ndarray:
    x0, y0, x1, y1 = rectangle
    return source[y0:y1, x0:x1].copy()


def binary_dilate(mask: np.ndarray, iterations: int = 1) -> np.ndarray:
    result = mask.astype(bool)
    for _ in range(iterations):
        padded = np.pad(result, 1, mode="constant", constant_values=False)
        expanded = np.zeros_like(result)
        for offset_y in range(3):
            for offset_x in range(3):
                expanded |= padded[
                    offset_y : offset_y + result.shape[0],
                    offset_x : offset_x + result.shape[1],
                ]
        result = expanded
    return result


def binary_erode(mask: np.ndarray, iterations: int = 1) -> np.ndarray:
    result = mask.astype(bool)
    for _ in range(iterations):
        padded = np.pad(result, 1, mode="constant", constant_values=False)
        contracted = np.ones_like(result)
        for offset_y in range(3):
            for offset_x in range(3):
                contracted &= padded[
                    offset_y : offset_y + result.shape[0],
                    offset_x : offset_x + result.shape[1],
                ]
        result = contracted
    return result


def largest_component(mask: np.ndarray) -> np.ndarray:
    height, width = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    largest: list[tuple[int, int]] = []
    for y in range(height):
        for x in range(width):
            if not mask[y, x] or visited[y, x]:
                continue
            component: list[tuple[int, int]] = []
            queue = deque([(y, x)])
            visited[y, x] = True
            while queue:
                current_y, current_x = queue.popleft()
                component.append((current_y, current_x))
                for next_y, next_x in (
                    (current_y - 1, current_x),
                    (current_y + 1, current_x),
                    (current_y, current_x - 1),
                    (current_y, current_x + 1),
                ):
                    if (
                        0 <= next_y < height
                        and 0 <= next_x < width
                        and mask[next_y, next_x]
                        and not visited[next_y, next_x]
                    ):
                        visited[next_y, next_x] = True
                        queue.append((next_y, next_x))
            if len(component) > len(largest):
                largest = component
    result = np.zeros_like(mask, dtype=bool)
    if largest:
        ys, xs = zip(*largest)
        result[np.asarray(ys), np.asarray(xs)] = True
    return result


def fill_holes(mask: np.ndarray) -> np.ndarray:
    inverse = ~mask
    height, width = mask.shape
    exterior = np.zeros_like(mask, dtype=bool)
    queue: deque[tuple[int, int]] = deque()
    for x in range(width):
        if inverse[0, x]:
            queue.append((0, x))
        if inverse[height - 1, x]:
            queue.append((height - 1, x))
    for y in range(height):
        if inverse[y, 0]:
            queue.append((y, 0))
        if inverse[y, width - 1]:
            queue.append((y, width - 1))
    while queue:
        y, x = queue.popleft()
        if exterior[y, x] or not inverse[y, x]:
            continue
        exterior[y, x] = True
        for next_y, next_x in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= next_y < height and 0 <= next_x < width and not exterior[next_y, next_x]:
                queue.append((next_y, next_x))
    return mask | (inverse & ~exterior)


def segment_helmet(crop: np.ndarray) -> tuple[np.ndarray, list[str]]:
    rgb = crop[:, :, :3]
    maximum = rgb.max(axis=2)
    minimum = rgb.min(axis=2)
    saturation_delta = maximum - minimum
    mask = (
        (saturation_delta > cfg.PREPROCESS["saturation_threshold"])
        | (maximum < cfg.PREPROCESS["dark_threshold"])
    )
    margin = cfg.PREPROCESS["crop_margin_px"]
    mask[:margin, :] = False
    mask[-margin:, :] = False
    mask[:, :margin] = False
    mask[:, -margin:] = False
    mask = binary_erode(binary_dilate(mask, cfg.PREPROCESS["close_iterations"]), cfg.PREPROCESS["close_iterations"])
    mask = largest_component(mask)
    mask = fill_holes(mask)
    mask = binary_dilate(binary_erode(mask, 1), 1)
    warnings = []
    coverage = float(mask.mean())
    if coverage < 0.12 or coverage > 0.80:
        warnings.append(f"Unusual foreground coverage: {coverage:.4f}")
    return mask, warnings


def mask_bbox(mask: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        raise RuntimeError("Reference segmentation produced an empty mask")
    return int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1)


def profile_samples(mask: np.ndarray, bbox: tuple[int, int, int, int]) -> list[dict]:
    x0, y0, x1, y1 = bbox
    sample_count = cfg.PREPROCESS["profile_sample_count"]
    band = cfg.PREPROCESS["profile_band_half_height_px"]
    samples = []
    for index in range(sample_count):
        normalized_y = index / (sample_count - 1)
        row = int(round(y0 + normalized_y * max(1, y1 - y0 - 1)))
        low = max(y0, row - band)
        high = min(y1, row + band + 1)
        columns = np.flatnonzero(mask[low:high].any(axis=0))
        if len(columns) == 0:
            left = right = int(round((x0 + x1 - 1) * 0.5))
        else:
            left = int(columns.min())
            right = int(columns.max())
        bbox_width = max(1, x1 - x0)
        samples.append(
            {
                "t_from_top": round(normalized_y, 6),
                "row_px": row,
                "left_px": left,
                "right_px": right,
                "left_norm": round((left - x0) / bbox_width, 6),
                "right_norm": round((right - x0 + 1) / bbox_width, 6),
                "width_fraction": round((right - left + 1) / bbox_width, 6),
            }
        )
    return samples


def mask_preview(mask: np.ndarray) -> np.ndarray:
    rgba = np.zeros((mask.shape[0], mask.shape[1], 4), dtype=np.float32)
    rgba[:, :, :3] = mask[:, :, None].astype(np.float32)
    rgba[:, :, 3] = 1.0
    return rgba


def main() -> None:
    started = time.perf_counter()
    cfg.DERIVED_REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    cfg.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    sources = {}
    source_dimensions = {}
    for key, path in cfg.SOURCE_REFERENCES.items():
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(f"Missing required reference: {path}")
        sources[key] = load_rgba_top_origin(path)
        source_dimensions[key] = [int(sources[key].shape[1]), int(sources[key].shape[0])]

    measurements = {
        "project": "AEGIS-R7 graybox v3 reference measurements",
        "source_dimensions_px": source_dimensions,
        "coordinate_convention": "crop coordinates use top-left origin; t_from_top is 0 at helmet top",
        "landmarks_normalized": cfg.PREPROCESS["landmarks"],
        "views": {},
        "preprocessing_warnings": [],
    }
    for view_name, (source_key, rectangle, make_mask) in cfg.REFERENCE_CROPS.items():
        crop = crop_image(sources[source_key], rectangle)
        crop_path = cfg.DERIVED_REFERENCE_DIR / f"{view_name}_crop.png"
        save_rgba_top_origin(crop_path, crop)
        view_data = {
            "source": source_key,
            "source_rect_top_origin_px": list(rectangle),
            "crop_path": str(crop_path),
            "crop_size_px": [int(crop.shape[1]), int(crop.shape[0])],
        }
        if make_mask:
            mask, warnings = segment_helmet(crop)
            bbox = mask_bbox(mask)
            mask_path = cfg.DERIVED_REFERENCE_DIR / f"{view_name}_mask.png"
            save_rgba_top_origin(mask_path, mask_preview(mask))
            bbox_width = bbox[2] - bbox[0]
            bbox_height = bbox[3] - bbox[1]
            view_data.update(
                {
                    "mask_path": str(mask_path),
                    "silhouette_bbox_px": list(bbox),
                    "silhouette_height_to_width_ratio": round(bbox_height / bbox_width, 6),
                    "foreground_coverage": round(float(mask.mean()), 6),
                    "profiles": profile_samples(mask, bbox),
                    "warnings": warnings,
                }
            )
            measurements["preprocessing_warnings"].extend(
                f"{view_name}: {warning}" for warning in warnings
            )
        measurements["views"][view_name] = view_data

    cfg.MEASUREMENTS_PATH.write_text(
        json.dumps(measurements, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    elapsed = time.perf_counter() - started
    log = {
        "script": str(Path(__file__).resolve()),
        "elapsed_seconds": round(elapsed, 4),
        "output_measurements": str(cfg.MEASUREMENTS_PATH),
        "derived_reference_dir": str(cfg.DERIVED_REFERENCE_DIR),
        "crop_count": len(cfg.REFERENCE_CROPS),
        "mask_count": sum(1 for _, _, make_mask in cfg.REFERENCE_CROPS.values() if make_mask),
    }
    (cfg.LOG_DIR / "prepare_references.json").write_text(
        json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"AEGIS_V3_REFERENCES_OK crops={log['crop_count']} masks={log['mask_count']} elapsed={elapsed:.3f}s")
    print(f"AEGIS_V3_MEASUREMENTS={cfg.MEASUREMENTS_PATH}")


if __name__ == "__main__":
    main()
