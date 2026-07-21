"""Central configuration for the AEGIS-R7 procedural graybox.

All values are Blender metres/radians unless a key explicitly says otherwise.
Coordinates are X right, Y back, Z up; the helmet faces -Y.
"""

from pathlib import Path


BLENDER_DIR = Path(__file__).resolve().parent.parent
REFERENCE_DIR = BLENDER_DIR / "references"
OUTPUT_DIR = BLENDER_DIR / "output" / "graybox"
HELMET_ONLY_DIR = OUTPUT_DIR / "helmet_only"
WITH_HEAD_DIR = OUTPUT_DIR / "with_head_proxy"
LOG_DIR = OUTPUT_DIR / "logs"
WORK_DIR = BLENDER_DIR / "work"
BLEND_PATH = WORK_DIR / "helmet_graybox.blend"
GLB_PATH = OUTPUT_DIR / "helmet_graybox.glb"
REPORT_PATH = OUTPUT_DIR / "helmet_graybox_report.json"

REFERENCE_PATHS = {
    "helmet_turnaround.png": REFERENCE_DIR / "helmet_turnaround.png",
    "helmet_assembly_reference.png": REFERENCE_DIR / "helmet_assembly_reference.png",
}

MANAGED_COLLECTIONS = ("REFERENCE", "HEAD_PROXY", "HELMET", "RENDER")

REQUIRED_PARTS = (
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

# The proxy is deliberately simple, but its combined bounds describe a neutral
# adult head including ears, jaw, chin and neck.
HEAD = {
    "total_height": 0.235,
    "main_width": 0.160,
    "front_back_depth": 0.200,
    "neck_diameter": 0.112,
    "base_clearance": 0.012,
    "cranium_center": (0.0, 0.004, 0.025),
    "cranium_radii": (0.080, 0.100, 0.105),
    "face_center": (0.0, -0.025, -0.041),
    "face_radii": (0.066, 0.086, 0.063),
    "jaw_center": (0.0, -0.019, -0.073),
    "jaw_radii": (0.058, 0.068, 0.041),
    "chin_center": (0.0, -0.057, -0.094),
    "chin_radii": (0.034, 0.033, 0.019),
    "ear_center_z": -0.003,
    "ear_center_y": 0.004,
    "ear_center_x": 0.081,
    "ear_radii": (0.0065, 0.017, 0.029),
    "neck_center": (0.0, 0.010, -0.157),
    "neck_depth": 0.118,
}

HELMET = {
    # Reported ratio is also recomputed from evaluated geometry at export.
    "body_width": 0.228,
    "body_height": 0.269,
    "body_ratio_target": 1.18,
    "center_z": 0.007,
    "outer_radii": (0.1125, 0.1320, 0.1400),
    "inner_radii": (0.0970, 0.1160, 0.1380),
    "shell_open_theta": 2.36,
    "shell_thickness": 0.0040,
    "panel_thickness": 0.0035,
    "panel_bevel": 0.0014,
    # Ellipsoid armor patches: (theta range, phi range, radii override).
    # Narrow gaps expose the darker inner shell as deliberate panel seams.
    "patches": {
        "CrownFront": ((0.10, 0.90), (-3.12, -0.02), None),
        "CrownRear": ((0.10, 0.94), (0.02, 3.12), None),
        "RearShell_R": ((0.86, 2.26), (0.02, 1.555), None),
        "RearShell_L": ((0.86, 2.26), (1.587, 3.12), None),
        "Temple_R": ((0.84, 1.48), (-1.555, -0.02), None),
        "Temple_L": ((0.84, 1.48), (-3.12, -1.587), None),
        "Cheek_R": ((1.42, 2.24), (-1.555, -0.02), (0.1140, 0.1335, 0.1410)),
        "Cheek_L": ((1.42, 2.24), (-3.12, -1.587), (0.1140, 0.1335, 0.1410)),
    },
    # (z, half width, base y) rows; the builder bows each row toward -Y.
    "faceplate_rows": (
        (0.093, 0.061, -0.110),
        (0.076, 0.078, -0.116),
        (0.050, 0.084, -0.125),
        (0.026, 0.082, -0.133),
        (0.004, 0.064, -0.138),
        (-0.025, 0.050, -0.141),
        (-0.052, 0.040, -0.137),
        (-0.077, 0.030, -0.128),
    ),
    "faceplate_columns": 9,
    "faceplate_bow": 0.006,
    "faceplate_lower_bow": 0.004,
    "faceplate_top_peak": 0.006,
    "jaw_polygon_r": (
        (0.041, -0.050),
        (0.082, -0.060),
        (0.086, -0.083),
        (0.064, -0.116),
        (0.040, -0.122),
        (0.027, -0.097),
    ),
    "jaw_y_front": -0.136,
    "jaw_y_back": -0.111,
    "chin_polygon": (
        (-0.039, -0.079),
        (0.039, -0.079),
        (0.045, -0.099),
        (0.030, -0.122),
        (-0.030, -0.122),
        (-0.045, -0.099),
    ),
    "chin_y_front": -0.143,
    "chin_y_back": -0.113,
    # Eye-slot center is 41.8% down from the nominal body top.
    "eye_center_z": 0.029,
    "eye_center_x": 0.049,
    "eye_width": 0.055,
    "eye_height": 0.010,
    "eye_angle_degrees": 7.0,
    "eye_corner_bevel": 0.0035,
    "eye_inner_scale": 0.76,
    "eye_housing_y_front": -0.148,
    "eye_housing_y_back": -0.139,
    "eye_lens_y_front": -0.145,
    "eye_lens_y_back": -0.140,
    # Ear diameter is about ten percent smaller than the concept reference.
    "ear_center_x": 0.113,
    "ear_center_y": 0.004,
    "ear_center_z": -0.001,
    "ear_radius": 0.0305,
    "ear_depth": 0.012,
    "ear_core_radius": 0.020,
    "ear_core_depth": 0.005,
    "neck_ring_radius_x": 0.079,
    "neck_ring_radius_y": 0.070,
    "neck_ring_tube": 0.0085,
    "neck_ring_z": -0.104,
}

MATERIALS = {
    "InnerShell": {"color": (0.007, 0.010, 0.016, 1.0), "roughness": 0.55},
    "ArmorGray": {"color": (0.036, 0.044, 0.058, 1.0), "roughness": 0.50},
    "FaceplateGray": {"color": (0.115, 0.132, 0.158, 1.0), "roughness": 0.46},
    "EyeHousingDark": {"color": (0.004, 0.007, 0.012, 1.0), "roughness": 0.40},
    "EyeLensBlueGray": {"color": (0.055, 0.190, 0.285, 1.0), "roughness": 0.32},
    "NeckRingDark": {"color": (0.009, 0.014, 0.022, 1.0), "roughness": 0.55},
    "HeadProxyGray": {"color": (0.090, 0.105, 0.125, 0.40), "roughness": 0.62},
}

RENDER = {
    "resolution": 1024,
    "ortho_scale": 0.350,
    "perspective_lens": 72.0,
    "world_color": (0.220, 0.240, 0.280, 1.0),
    "world_strength": 0.55,
    "target": (0.0, 0.0, 0.004),
    "lights": (
        ("Key_Light", (0.44, -0.52, 0.52), 20.0, 0.42),
        ("Fill_Light", (-0.48, -0.28, 0.20), 9.0, 0.48),
        ("Rim_Light", (0.34, 0.52, 0.42), 14.0, 0.38),
        ("Rear_Fill_Light", (-0.38, 0.48, 0.02), 7.0, 0.42),
    ),
    "fit_review_alpha": {
        "InnerShell": 0.13,
        "ArmorGray": 0.25,
        "FaceplateGray": 0.21,
        "EyeHousingDark": 0.52,
        "EyeLensBlueGray": 0.68,
        "NeckRingDark": 0.28,
        "HeadProxyGray": 0.78,
    },
}

CAMERAS = {
    "front": {"object": "Camera_Front", "location": (0.0, -1.0, 0.004), "type": "ORTHO"},
    "back": {"object": "Camera_Back", "location": (0.0, 1.0, 0.004), "type": "ORTHO"},
    "left": {"object": "Camera_Left", "location": (-1.0, 0.0, 0.004), "type": "ORTHO"},
    "right": {"object": "Camera_Right", "location": (1.0, 0.0, 0.004), "type": "ORTHO"},
    "top": {"object": "Camera_Top", "location": (0.0, 0.0, 1.0), "type": "ORTHO"},
    "bottom": {"object": "Camera_Bottom", "location": (0.0, 0.0, -1.0), "type": "ORTHO"},
    "front_3q": {"object": "Camera_Front3Q", "location": (0.44, -0.62, 0.25), "type": "PERSP"},
    "rear_3q": {"object": "Camera_Rear3Q", "location": (-0.44, 0.62, 0.23), "type": "PERSP"},
}

REVIEW_NAMES = tuple(CAMERAS.keys())
