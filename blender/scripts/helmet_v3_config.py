"""Central configuration for the reference-driven AEGIS-R7 graybox v3."""

from __future__ import annotations

from pathlib import Path


BLENDER_DIR = Path(__file__).resolve().parent.parent
SCRIPT_DIR = BLENDER_DIR / "scripts"
REFERENCE_DIR = BLENDER_DIR / "references"
DERIVED_REFERENCE_DIR = REFERENCE_DIR / "derived_v3"
OUTPUT_DIR = BLENDER_DIR / "output" / "graybox_v3"
LOG_DIR = OUTPUT_DIR / "logs"
WORK_DIR = BLENDER_DIR / "work"
BLEND_PATH = WORK_DIR / "helmet_graybox_v3.blend"
GLB_PATH = OUTPUT_DIR / "helmet_graybox_v3.glb"
REPORT_PATH = OUTPUT_DIR / "helmet_graybox_v3_report.json"
MEASUREMENTS_PATH = OUTPUT_DIR / "reference_measurements.json"
BASELINE_COMMIT = "684228c2b90f7246d15fa67a4b3b0ff4293160c5"

SOURCE_REFERENCES = {
    "turnaround": REFERENCE_DIR / "helmet_turnaround.png",
    "assembly": REFERENCE_DIR / "helmet_assembly_reference.png",
}

# Rectangles are top-origin pixel coordinates: x0, y0, x1, y1.
REFERENCE_CROPS = {
    "front": ("turnaround", (0, 0, 512, 512), True),
    "back": ("turnaround", (512, 0, 1024, 512), True),
    "right": ("turnaround", (1024, 0, 1536, 512), True),
    "left": ("turnaround", (0, 512, 512, 1024), True),
    "top": ("turnaround", (512, 512, 1024, 1024), True),
    "bottom": ("turnaround", (1024, 512, 1536, 1024), True),
    "closed_front_3q": ("assembly", (0, 0, 512, 512), True),
    "closed_rear_3q": ("assembly", (512, 0, 1024, 512), True),
    "open_front": ("assembly", (1024, 0, 1536, 512), False),
    "exploded_front": ("assembly", (0, 512, 512, 1024), False),
    "exploded_rear": ("assembly", (512, 512, 1024, 1024), False),
    "exploded_labeled": ("assembly", (1024, 512, 1536, 1024), False),
}

PREPROCESS = {
    "crop_margin_px": 5,
    "saturation_threshold": 0.075,
    "dark_threshold": 0.60,
    "close_iterations": 3,
    "profile_sample_count": 29,
    "profile_band_half_height_px": 2,
    "landmarks": {
        "eye_line_from_top": 0.43,
        "faceplate_top_from_top": 0.19,
        "faceplate_bottom_from_top": 0.79,
        "ear_cover_center_from_top": 0.50,
        "jaw_bottom_from_top": 0.94,
    },
}

MANAGED_COLLECTIONS = (
    "REFERENCE_V3",
    "HEAD_PROXY_V3",
    "HELMET_V3",
    "MODELING_V3",
    "RENDER_V3",
)

REQUIRED_EXPORT_PARTS = (
    "HelmetRoot",
    "InnerShell",
    "CrownFront",
    "CrownRear",
    "RearShell_L",
    "RearShell_R",
    "Temple_L",
    "Temple_R",
    "EarCover_L",
    "EarCover_R",
    "Cheek_L",
    "Cheek_R",
    "Jaw_L",
    "Jaw_R",
    "Chin",
    "Faceplate",
    "EyeHousing_L",
    "EyeHousing_R",
    "EyeLens_L",
    "EyeLens_R",
    "NeckRingFront",
    "NeckRingRear",
)

HEAD = {
    "height": 0.235,
    "width": 0.160,
    "depth": 0.200,
    "cranium_center": (0.0, 0.006, 0.025),
    "cranium_radii": (0.080, 0.100, 0.105),
    "face_center": (0.0, -0.030, -0.040),
    "face_radii": (0.065, 0.083, 0.061),
    "jaw_center": (0.0, -0.024, -0.075),
    "jaw_radii": (0.057, 0.065, 0.040),
    "chin_center": (0.0, -0.058, -0.097),
    "chin_radii": (0.033, 0.032, 0.018),
    "nose_center": (0.0, -0.111, -0.027),
    "nose_radii": (0.019, 0.018, 0.029),
    "ear_center": (0.081, 0.006, -0.004),
    "ear_radii": (0.0065, 0.016, 0.029),
    "neck_center": (0.0, 0.012, -0.160),
    "neck_radius": 0.055,
    "neck_depth": 0.120,
}

# These values scale normalized profiles read from reference_measurements.json.
# They are intentionally separate front/side controls rather than ellipsoid radii.
FIT = {
    "top_z": 0.151,
    "bottom_z": -0.133,
    "max_body_width": 0.224,
    "reference_depth": 0.258,
    "front_bias_y": -0.006,
    "ring_count": 55,
    "ring_vertices": 96,
    "superellipse_crown": 2.40,
    "superellipse_mid": 3.35,
    "superellipse_lower": 3.75,
    "profile_smoothing_passes": 3,
    "minimum_half_width": 0.018,
    "minimum_half_depth": 0.022,
    "master_subdivision_levels": 1,
    "inner_clearance_x": 0.014,
    "inner_clearance_front": 0.010,
    "inner_clearance_rear": 0.007,
    "inner_clearance_top": 0.004,
    "inner_face_support_bulge": 0.013,
    "panel_offset": 0.0028,
    "panel_thickness": 0.0032,
    "panel_gap_t": 0.012,
    "panel_gap_angle_degrees": 2.2,
    "faceplate_center_bulge": 0.0075,
    "faceplate_edge_offset": 0.0015,
    "faceplate_thickness": 0.0032,
    "eye_inset": 0.0022,
    "eye_width": 0.046,
    "eye_height": 0.0080,
    "eye_angle_degrees": 7.0,
    "ear_radius": 0.027,
    "ear_face_radius": 0.019,
    "ear_depth": 0.011,
    "neck_ring_z": -0.124,
    "neck_ring_outer_x": 0.072,
    "neck_ring_outer_y": 0.056,
    "neck_ring_radial_width": 0.007,
    "neck_ring_height": 0.012,
}

MATERIALS = {
    "ClayInner": {"base": (0.020, 0.025, 0.034, 1.0), "roughness": 0.58, "metallic": 0.0},
    "ClayArmorA": {"base": (0.105, 0.120, 0.145, 1.0), "roughness": 0.52, "metallic": 0.0},
    "ClayArmorB": {"base": (0.070, 0.082, 0.102, 1.0), "roughness": 0.54, "metallic": 0.0},
    "ClayFaceplate": {"base": (0.240, 0.260, 0.295, 1.0), "roughness": 0.46, "metallic": 0.0},
    "ClayHousing": {"base": (0.005, 0.008, 0.014, 1.0), "roughness": 0.44, "metallic": 0.0},
    "ClayLens": {"base": (0.060, 0.250, 0.390, 1.0), "roughness": 0.28, "metallic": 0.0},
    "Proxy": {"base": (0.120, 0.135, 0.155, 1.0), "roughness": 0.62, "metallic": 0.0},
    "DesignRed": {"base": (0.220, 0.010, 0.016, 1.0), "roughness": 0.29, "metallic": 0.72},
    "DesignRedDark": {"base": (0.095, 0.006, 0.011, 1.0), "roughness": 0.34, "metallic": 0.70},
    "DesignGold": {"base": (0.390, 0.205, 0.055, 1.0), "roughness": 0.25, "metallic": 0.82},
    "DesignGunmetal": {"base": (0.012, 0.018, 0.027, 1.0), "roughness": 0.33, "metallic": 0.62},
    "DesignLens": {"base": (0.030, 0.350, 0.620, 1.0), "roughness": 0.18, "metallic": 0.10, "emission": (0.08, 0.65, 1.0, 1.0), "emission_strength": 2.2},
    "Silhouette": {"base": (0.0, 0.0, 0.0, 1.0), "roughness": 1.0, "metallic": 0.0},
}

PART_MATERIALS = {
    "InnerShell": ("ClayInner", "DesignGunmetal"),
    "CrownFront": ("ClayArmorA", "DesignRed"),
    "CrownRear": ("ClayArmorB", "DesignRedDark"),
    "RearShell_L": ("ClayArmorA", "DesignRed"),
    "RearShell_R": ("ClayArmorA", "DesignRed"),
    "Temple_L": ("ClayArmorB", "DesignRedDark"),
    "Temple_R": ("ClayArmorB", "DesignRedDark"),
    "Cheek_L": ("ClayArmorA", "DesignRed"),
    "Cheek_R": ("ClayArmorA", "DesignRed"),
    "Jaw_L": ("ClayArmorB", "DesignRedDark"),
    "Jaw_R": ("ClayArmorB", "DesignRedDark"),
    "Chin": ("ClayArmorA", "DesignRed"),
    "Faceplate": ("ClayFaceplate", "DesignGold"),
    "EarCover_L": ("ClayArmorB", "DesignRedDark"),
    "EarCover_R": ("ClayArmorB", "DesignRedDark"),
    "EarEmitter_L": ("ClayLens", "DesignLens"),
    "EarEmitter_R": ("ClayLens", "DesignLens"),
    "EyeHousing_L": ("ClayHousing", "DesignGunmetal"),
    "EyeHousing_R": ("ClayHousing", "DesignGunmetal"),
    "EyeLens_L": ("ClayLens", "DesignLens"),
    "EyeLens_R": ("ClayLens", "DesignLens"),
    "NeckRingFront": ("ClayInner", "DesignGunmetal"),
    "NeckRingRear": ("ClayInner", "DesignGunmetal"),
}

RENDER = {
    "resolution": 1024,
    "silhouette_resolution": 512,
    "ortho_scale": 0.355,
    "lens": 72.0,
    "world_color": (0.18, 0.20, 0.24, 1.0),
    "world_strength": 0.55,
    "target": (0.0, -0.002, 0.006),
    "lights": (
        ("Key_V3", (0.46, -0.54, 0.54), 34.0, 0.42),
        ("Fill_V3", (-0.42, -0.32, 0.20), 15.0, 0.50),
        ("Rim_V3", (0.36, 0.50, 0.40), 25.0, 0.38),
        ("RearFill_V3", (-0.35, 0.45, 0.04), 12.0, 0.44),
    ),
}

CAMERAS = {
    "front": ("CameraV3_Front", (0.0, -1.0, 0.006), "ORTHO"),
    "back": ("CameraV3_Back", (0.0, 1.0, 0.006), "ORTHO"),
    "left": ("CameraV3_Left", (-1.0, 0.0, 0.006), "ORTHO"),
    "right": ("CameraV3_Right", (1.0, 0.0, 0.006), "ORTHO"),
    "top": ("CameraV3_Top", (0.0, 0.0, 1.0), "ORTHO"),
    "bottom": ("CameraV3_Bottom", (0.0, 0.0, -1.0), "ORTHO"),
    "front_3q": ("CameraV3_Front3Q", (0.44, -0.62, 0.25), "PERSP"),
    "rear_3q": ("CameraV3_Rear3Q", (-0.44, 0.62, 0.23), "PERSP"),
}

ORTHO_REFERENCE_VIEWS = ("front", "right", "left", "top")
REVIEW_VIEWS = tuple(CAMERAS.keys())
