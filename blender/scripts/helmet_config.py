"""Central configuration for the AEGIS-R7 procedural graybox."""

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

# Blender coordinates: X left/right, Y front/back, Z up. Front is -Y.
HEAD = {
    "total_height": 0.235,
    "main_width": 0.160,
    "front_back_depth": 0.200,
    "neck_diameter": 0.112,
    "base_clearance": 0.012,
    "cranium_center": (0.0, 0.0, 0.025),
    "cranium_radii": (0.080, 0.100, 0.105),
    "face_center": (0.0, -0.020, -0.045),
    "face_radii": (0.066, 0.087, 0.060),
    "ear_center_z": -0.002,
    "ear_center_y": 0.002,
    "ear_radii": (0.007, 0.018, 0.030),
    "neck_center_z": -0.154,
    "neck_depth": 0.120,
}

HELMET = {
    "body_width": 0.222,
    "body_height": 0.262,
    "body_ratio_target": 1.18,
    "center_z": 0.008,
    "outer_radii": (0.111, 0.132, 0.140),
    "inner_radii": (0.098, 0.116, 0.139),
    "shell_open_theta": 2.35,
    "shell_thickness": 0.004,
    "panel_thickness": 0.0035,
    "panel_bevel": 0.0016,
    "ear_center_x": 0.119,
    "ear_center_y": 0.005,
    "ear_center_z": 0.002,
    "ear_radius": 0.034,
    "ear_depth": 0.014,
    "eye_center_z": 0.032,
    "eye_center_x": 0.050,
    "eye_width": 0.055,
    "eye_height": 0.010,
    "eye_angle_degrees": 7.0,
    "neck_ring_radius": 0.080,
    "neck_ring_tube": 0.010,
    "neck_ring_z": -0.098,
}

MATERIALS = {
    "InnerShell": {"color": (0.022, 0.030, 0.043, 1.0), "roughness": 0.46},
    "ArmorGray": {"color": (0.085, 0.105, 0.135, 1.0), "roughness": 0.40},
    "FaceplateGray": {"color": (0.240, 0.270, 0.315, 1.0), "roughness": 0.38},
    "EyeHousingDark": {"color": (0.006, 0.010, 0.017, 1.0), "roughness": 0.32},
    "EyeLensBlueGray": {"color": (0.055, 0.220, 0.360, 1.0), "roughness": 0.24},
    "NeckRingDark": {"color": (0.018, 0.026, 0.039, 1.0), "roughness": 0.46},
    "HeadProxyGray": {"color": (0.140, 0.155, 0.180, 0.42), "roughness": 0.54},
}

RENDER = {
    "resolution": 1024,
    "ortho_scale": 0.355,
    "perspective_lens": 68.0,
    "world_color": (0.175, 0.190, 0.215, 1.0),
    "world_strength": 0.38,
    "target": (0.0, 0.0, 0.002),
}

CAMERAS = {
    "front": {"object": "Camera_Front", "location": (0.0, -1.0, 0.002), "type": "ORTHO"},
    "back": {"object": "Camera_Back", "location": (0.0, 1.0, 0.002), "type": "ORTHO"},
    "left": {"object": "Camera_Left", "location": (-1.0, 0.0, 0.002), "type": "ORTHO"},
    "right": {"object": "Camera_Right", "location": (1.0, 0.0, 0.002), "type": "ORTHO"},
    "top": {"object": "Camera_Top", "location": (0.0, 0.0, 1.0), "type": "ORTHO"},
    "bottom": {"object": "Camera_Bottom", "location": (0.0, 0.0, -1.0), "type": "ORTHO"},
    "front_3q": {"object": "Camera_Front3Q", "location": (0.62, -0.78, 0.31), "type": "PERSP"},
    "rear_3q": {"object": "Camera_Rear3Q", "location": (-0.62, 0.78, 0.29), "type": "PERSP"},
}

REVIEW_NAMES = tuple(CAMERAS.keys())
