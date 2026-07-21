"""Configuration for AEGIS-R7 graybox v4 feature projection.

The v4 pipeline intentionally reuses the measured v3 envelope and camera
coordinate system while routing every generated artifact to an isolated v4
workspace.  Surface-feature dimensions below are derived from crop pixels by
``extract_features_v4.py``.
"""

from __future__ import annotations

from copy import deepcopy

from helmet_v3_config import *  # noqa: F401,F403 - v3 is the protected envelope baseline


OUTPUT_DIR = BLENDER_DIR / "output" / "graybox_v4"
LOG_DIR = OUTPUT_DIR / "logs"
BLEND_PATH = WORK_DIR / "helmet_graybox_v4.blend"
GLB_PATH = OUTPUT_DIR / "helmet_graybox_v4.glb"
REPORT_PATH = OUTPUT_DIR / "helmet_graybox_v4_report.json"
FEATURE_CURVES_PATH = OUTPUT_DIR / "feature_curves.json"
FEATURE_VALIDATION_PATH = OUTPUT_DIR / "feature_validation.json"
MEASUREMENTS_PATH = BLENDER_DIR / "output" / "graybox_v3" / "reference_measurements.json"
BASELINE_COMMIT = "e480eec"

# The imported v3 builder contains collection-name literals.  These names live
# only inside the separate v4 .blend and therefore cannot mutate the v3 asset.
MANAGED_COLLECTIONS = tuple(MANAGED_COLLECTIONS)

FIT = deepcopy(FIT)
FIT.update(
    {
        "faceplate_center_bulge": 0.0060,
        "faceplate_edge_offset": 0.0020,
        "faceplate_thickness": 0.0030,
        "eye_inset": 0.0030,
        "eye_width": 0.0520,
        "eye_height": 0.0095,
        "eye_angle_degrees": 5.0,
        "ear_radius": 0.0560,
        "ear_face_radius": 0.0300,
        "ear_depth": 0.0100,
        "bezel_width": 0.0058,
        "spine_width": 0.0360,
        "feature_offset": 0.0038,
    }
)

MATERIALS = deepcopy(MATERIALS)
MATERIALS.update(
    {
        "ClayAccent": {"base": (0.030, 0.038, 0.050, 1.0), "roughness": 0.50, "metallic": 0.0},
        "DesignBlack": {"base": (0.004, 0.006, 0.010, 1.0), "roughness": 0.42, "metallic": 0.35},
        "DesignVent": {"base": (0.002, 0.004, 0.007, 1.0), "roughness": 0.46, "metallic": 0.45},
    }
)

PART_MATERIALS = deepcopy(PART_MATERIALS)
for _name in (
    "FaceplateBezel",
    "CrownSpine_01",
    "CrownSpine_02",
    "CrownSpine_03",
    "CrownSpine_04",
    "RearSpine_01",
    "RearSpine_02",
    "RearSpine_03",
    "RearSpine_04",
    "RearSpine_05",
    "RearSpine_06",
    "RearSpine_07",
    "RearSpine_08",
    "CrownSpine_Cap",
    "CrownCenterCover",
    "RearVent_L_Upper",
    "RearVent_R_Upper",
    "RearVent_L_Lower",
    "RearVent_R_Lower",
    "RearLayer_L_Upper",
    "RearLayer_R_Upper",
    "EarRing_L_Outer",
    "EarRing_R_Outer",
    "EarRing_L_Inner",
    "EarRing_R_Inner",
    "JawTrim_L",
    "JawTrim_R",
    "LowerUTrim",
    "RearLayer_R_Lower",
    "RearLayer_L_Lower",
    "SideCheekPanel_L",
    "SideCheekPanel_R",
    "SideJawPanel_L",
    "SideJawPanel_R",
    "SideGroove_L",
    "SideGroove_R",
):
    PART_MATERIALS[_name] = ("ClayAccent", "DesignBlack")

for _name in ("SideCheekPanel_L", "SideCheekPanel_R"):
    PART_MATERIALS[_name] = ("ClayArmorA", "DesignRed")
for _name in ("SideJawPanel_L", "SideJawPanel_R"):
    PART_MATERIALS[_name] = ("ClayArmorB", "DesignRedDark")
PART_MATERIALS["CrownCenterCover"] = ("ClayArmorA", "DesignRed")

REQUIRED_EXPORT_PARTS = tuple(REQUIRED_EXPORT_PARTS) + ("EarEmitter_L", "EarEmitter_R")

RENDER = deepcopy(RENDER)
RENDER.update({"resolution": 768, "silhouette_resolution": 512})

CAMERAS = deepcopy(CAMERAS)
REVIEW_VIEWS = tuple(CAMERAS.keys())

FEATURE_TOLERANCES_PERCENT = {
    "eye_center": 3.0,
    "eye_length_relative": 15.0,
    "ear_center": 4.0,
    "ear_radius_relative": 15.0,
    "faceplate_max_width": 4.0,
    "chin_tip_height": 3.0,
    "spine_width_relative": 30.0,
}
