"""Audit, export, clean-reimport, and report the AEGIS-R7 graybox v3."""

from __future__ import annotations

import json
import math
import platform
import subprocess
import sys
import time
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import helmet_v3_config as cfg


REPO_ROOT = cfg.BLENDER_DIR.parent
SYMMETRY_PAIRS = (
    ("RearShell_L", "RearShell_R"),
    ("Temple_L", "Temple_R"),
    ("Cheek_L", "Cheek_R"),
    ("Jaw_L", "Jaw_R"),
    ("EarCover_L", "EarCover_R"),
    ("EarEmitter_L", "EarEmitter_R"),
    ("EyeHousing_L", "EyeHousing_R"),
    ("EyeLens_L", "EyeLens_R"),
)

SEAM_PAIRS = (
    ("CrownFront", "Faceplate"),
    ("CrownFront", "CrownRear"),
    ("Faceplate", "Temple_L"),
    ("Faceplate", "Temple_R"),
    ("Faceplate", "Cheek_L"),
    ("Faceplate", "Cheek_R"),
    ("Cheek_L", "Jaw_L"),
    ("Cheek_R", "Jaw_R"),
    ("Jaw_L", "Chin"),
    ("Jaw_R", "Chin"),
    ("Temple_L", "RearShell_L"),
    ("Temple_R", "RearShell_R"),
    ("RearShell_L", "CrownRear"),
    ("RearShell_R", "CrownRear"),
)


def read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def git_value(*arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def evaluated_world_vertices(obj: bpy.types.Object, depsgraph) -> list[Vector]:
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        return [evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
    finally:
        evaluated.to_mesh_clear()


def mesh_island_count(bm: bmesh.types.BMesh) -> int:
    remaining = set(bm.verts)
    islands = 0
    while remaining:
        islands += 1
        stack = [remaining.pop()]
        while stack:
            vertex = stack.pop()
            for edge in vertex.link_edges:
                other = edge.other_vert(vertex)
                if other in remaining:
                    remaining.remove(other)
                    stack.append(other)
    return islands


def geometry_audit(
    objects: list[bpy.types.Object], depsgraph
) -> tuple[dict, dict[str, list[Vector]], dict[str, BVHTree]]:
    minimum = Vector((math.inf, math.inf, math.inf))
    maximum = Vector((-math.inf, -math.inf, -math.inf))
    total_triangles = 0
    object_health = {}
    world_vertices = {}
    surface_bvhs = {}
    for obj in objects:
        if obj.type != "MESH":
            continue
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            mesh.calc_loop_triangles()
            triangles = len(mesh.loop_triangles)
            total_triangles += triangles
            points = [evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
            world_vertices[obj.name] = points
            surface_bvhs[obj.name] = BVHTree.FromPolygons(
                points,
                [tuple(polygon.vertices) for polygon in mesh.polygons],
                all_triangles=False,
            )
            for point in points:
                for axis in range(3):
                    minimum[axis] = min(minimum[axis], point[axis])
                    maximum[axis] = max(maximum[axis], point[axis])

            bm = bmesh.new()
            try:
                bm.from_mesh(mesh)
                bm.normal_update()
                boundary_edges = sum(1 for edge in bm.edges if edge.is_boundary)
                non_manifold_edges = sum(1 for edge in bm.edges if not edge.is_manifold)
                degenerate_faces = sum(1 for face in bm.faces if face.calc_area() <= 1.0e-12)
                zero_normal_faces = sum(1 for face in bm.faces if face.normal.length <= 1.0e-9)
                islands = mesh_island_count(bm)
                signed_volume = None
                if non_manifold_edges == 0:
                    try:
                        signed_volume = float(bm.calc_volume(signed=True))
                    except ValueError:
                        signed_volume = None
            finally:
                bm.free()
            object_health[obj.name] = {
                "vertices": len(mesh.vertices),
                "triangles": triangles,
                "boundary_edges": boundary_edges,
                "non_manifold_edges": non_manifold_edges,
                "degenerate_faces": degenerate_faces,
                "zero_normal_faces": zero_normal_faces,
                "connected_mesh_islands": islands,
                "signed_volume_m3": None if signed_volume is None else round(signed_volume, 10),
                "reversed_closed_volume": bool(signed_volume is not None and signed_volume < -1.0e-10),
            }
        finally:
            evaluated.to_mesh_clear()

    dimensions = maximum - minimum
    audit = {
        "overall_bounding_box_m": {
            "minimum": [round(value, 6) for value in minimum],
            "maximum": [round(value, 6) for value in maximum],
            "width_x": round(dimensions.x, 6),
            "depth_y": round(dimensions.y, 6),
            "height_z": round(dimensions.z, 6),
        },
        "evaluated_triangle_count": total_triangles,
        "under_100k_triangle_target": total_triangles < 100000,
        "object_health": object_health,
        "totals": {
            "non_manifold_edges": sum(value["non_manifold_edges"] for value in object_health.values()),
            "degenerate_faces": sum(value["degenerate_faces"] for value in object_health.values()),
            "zero_normal_faces": sum(value["zero_normal_faces"] for value in object_health.values()),
            "reversed_closed_objects": [
                name for name, value in object_health.items() if value["reversed_closed_volume"]
            ],
        },
    }
    return audit, world_vertices, surface_bvhs


def kd_tree(points: list[Vector]) -> KDTree:
    tree = KDTree(len(points))
    for index, point in enumerate(points):
        tree.insert(point, index)
    tree.balance()
    return tree


def point_bounds(points: list[Vector]) -> dict:
    minimum = Vector((math.inf, math.inf, math.inf))
    maximum = Vector((-math.inf, -math.inf, -math.inf))
    for point in points:
        for axis in range(3):
            minimum[axis] = min(minimum[axis], point[axis])
            maximum[axis] = max(maximum[axis], point[axis])
    dimensions = maximum - minimum
    return {
        "minimum": [round(value, 6) for value in minimum],
        "maximum": [round(value, 6) for value in maximum],
        "width_x": round(dimensions.x, 6),
        "depth_y": round(dimensions.y, 6),
        "height_z": round(dimensions.z, 6),
    }


def nearest_distance(points_a: list[Vector], points_b: list[Vector]) -> float:
    if not points_a or not points_b:
        return math.inf
    tree = kd_tree(points_b)
    return min(tree.find(point)[2] for point in points_a)


def seam_audit(
    world_vertices: dict[str, list[Vector]],
    surface_bvhs: dict[str, BVHTree],
) -> dict:
    results = {}
    for first, second in SEAM_PAIRS:
        distance = min(
            nearest_distance(world_vertices[first], world_vertices[second]),
            nearest_distance(world_vertices[second], world_vertices[first]),
        )
        intersections = surface_bvhs[first].overlap(surface_bvhs[second])
        results[f"{first}__{second}"] = {
            "minimum_surface_distance_m": round(distance, 6),
            "minimum_surface_distance_mm": round(distance * 1000.0, 3),
            "intersecting_triangle_pair_count": len(intersections),
            "geometry_intersection_detected": bool(intersections),
            "classification": (
                "geometric_intersection" if intersections
                else "tight_contact_under_1_5_mm" if distance < 0.0015
                else "nominal_2_to_5_mm" if distance <= 0.0055
                else "mechanical_slot_5_to_8_mm" if distance <= 0.0085
                else "large_service_opening"
            ),
        }
    return results


def symmetry_audit(world_vertices: dict[str, list[Vector]]) -> dict:
    results = {}
    for left_name, right_name in SYMMETRY_PAIRS:
        right_tree = kd_tree(world_vertices[right_name])
        distances = []
        for point in world_vertices[left_name]:
            mirrored = Vector((-point.x, point.y, point.z))
            distances.append(right_tree.find(mirrored)[2])
        mean_distance = sum(distances) / max(1, len(distances))
        results[f"{left_name}__{right_name}"] = {
            "mean_mirror_error_m": round(mean_distance, 8),
            "maximum_mirror_error_m": round(max(distances, default=0.0), 8),
            "passes_0_5_mm_mean": mean_distance <= 0.0005,
        }
    return results


def floating_component_audit(
    world_vertices: dict[str, list[Vector]],
    master_vertices: list[Vector],
) -> dict:
    master_tree = kd_tree(master_vertices)
    results = {}
    skip = {"InnerShell", "NeckRingFront", "NeckRingRear"}
    for name, points in world_vertices.items():
        if name in skip:
            continue
        minimum = min(master_tree.find(point)[2] for point in points)
        results[name] = {
            "minimum_distance_to_master_m": round(minimum, 6),
            "floating_over_20_mm": minimum > 0.020,
        }
    return results


def transform_record(obj: bpy.types.Object) -> dict:
    return {
        "translation_m": [round(float(value), 6) for value in obj.location],
        "rotation_euler_rad": [round(float(value), 6) for value in obj.rotation_euler],
        "scale": [round(float(value), 6) for value in obj.scale],
        "translation_is_nonzero": obj.location.length > 1.0e-6,
    }


def pivot_audit(objects: list[bpy.types.Object]) -> dict:
    records = {obj.name: transform_record(obj) for obj in objects}
    movable = [obj for obj in objects if obj.type == "MESH" and obj.name != "InnerShell"]
    nonzero = [obj.name for obj in movable if obj.location.length > 1.0e-6]
    return {
        "object_transforms": records,
        "movable_mesh_count": len(movable),
        "movable_meshes_with_nonzero_translation": len(nonzero),
        "all_movable_meshes_have_nonzero_translation": len(nonzero) == len(movable),
        "nonzero_translation_nodes": nonzero,
        "negative_scale_nodes": [
            obj.name
            for obj in objects
            if any(value < 0.0 for value in obj.scale)
            or obj.matrix_world.to_3x3().determinant() < 0.0
        ],
        "non_unit_scale_nodes": [
            obj.name for obj in objects if any(abs(value - 1.0) > 1.0e-6 for value in obj.scale)
        ],
    }


def review_manifest() -> dict:
    groups = {
        "clay": (cfg.OUTPUT_DIR / "clay" / "helmet_only", 8),
        "design_preview": (cfg.OUTPUT_DIR / "design_preview" / "helmet_only", 8),
        "with_head_proxy": (cfg.OUTPUT_DIR / "with_head_proxy", 8),
        "wireframe": (cfg.OUTPUT_DIR / "wireframe", 3),
        "silhouette": (cfg.OUTPUT_DIR / "silhouette", 4),
        "exploded_sanity": (cfg.OUTPUT_DIR / "exploded_sanity", 1),
    }
    manifest = {}
    for label, (directory, expected) in groups.items():
        files = sorted(str(path) for path in directory.glob("*.png") if path.stat().st_size > 0)
        manifest[label] = {
            "directory": str(directory),
            "expected_count": expected,
            "actual_count": len(files),
            "complete": len(files) == expected,
            "files": files,
        }
    comparisons = [
        cfg.OUTPUT_DIR / name
        for name in (
            "comparison_front.png",
            "comparison_side.png",
            "comparison_front_3q.png",
            "comparison_contact_sheet.png",
            "overlay_front.png",
            "overlay_right.png",
            "overlay_left.png",
        )
    ]
    manifest["comparisons"] = {
        "complete": all(path.is_file() and path.stat().st_size > 0 for path in comparisons),
        "files": [str(path) for path in comparisons],
    }
    return manifest


def export_glb(objects: list[bpy.types.Object], root: bpy.types.Object) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.hide_set(False)
        obj.hide_render = False
        obj.select_set(True)
    bpy.context.view_layer.objects.active = root
    bpy.ops.export_scene.gltf(
        filepath=str(cfg.GLB_PATH),
        export_format="GLB",
        use_selection=True,
        export_apply=True,
        export_materials="EXPORT",
        export_cameras=False,
        export_lights=False,
        export_yup=True,
    )
    if not cfg.GLB_PATH.is_file() or cfg.GLB_PATH.stat().st_size == 0:
        raise RuntimeError(f"GLB export failed: {cfg.GLB_PATH}")


def clean_reimport() -> dict:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(cfg.GLB_PATH))
    imported = list(bpy.context.scene.objects)
    names = sorted(obj.name for obj in imported)
    transforms = {obj.name: transform_record(obj) for obj in imported}
    missing = [name for name in cfg.REQUIRED_EXPORT_PARTS if name not in names]
    forbidden = sorted(
        name
        for name in names
        if name.startswith(("ReferenceV3_", "HeadProxyV3_", "Camera_", "HelmetEnvelope_Master"))
        or name in {"Key_V3", "Fill_V3", "Rim_V3", "RearFill_V3"}
    )
    movable = [name for name in cfg.REQUIRED_EXPORT_PARTS if name not in {"HelmetRoot", "InnerShell"}]
    nonzero = [name for name in movable if name in transforms and transforms[name]["translation_is_nonzero"]]
    return {
        "success": not missing and not forbidden,
        "imported_object_count": len(imported),
        "imported_node_names": names,
        "missing_required_nodes": missing,
        "forbidden_non_export_nodes": forbidden,
        "node_transforms": transforms,
        "movable_required_nodes_with_nonzero_translation": len(nonzero),
        "movable_required_node_count": len(movable),
        "all_movable_required_nodes_have_nonzero_translation": len(nonzero) == len(movable),
    }


def main() -> None:
    started = time.perf_counter()
    if bpy.data.objects.get("HelmetRoot") is None:
        bpy.ops.wm.open_mainfile(filepath=str(cfg.BLEND_PATH))
    helmet_collection = bpy.data.collections.get("HELMET_V3")
    root = bpy.data.objects.get("HelmetRoot")
    master = bpy.data.objects.get("HelmetEnvelope_Master")
    if helmet_collection is None or root is None or master is None:
        raise RuntimeError("Missing HELMET_V3, HelmetRoot, or HelmetEnvelope_Master")

    export_objects = list(helmet_collection.all_objects)
    names = [obj.name for obj in export_objects]
    missing_required = [name for name in cfg.REQUIRED_EXPORT_PARTS if name not in names]
    if missing_required:
        raise RuntimeError(f"Missing required export nodes: {missing_required}")

    depsgraph = bpy.context.evaluated_depsgraph_get()
    geometry, world_vertices, surface_bvhs = geometry_audit(export_objects, depsgraph)
    master_vertices = evaluated_world_vertices(master, depsgraph)
    geometry["master_envelope_bounding_box_m"] = point_bounds(master_vertices)
    seams = seam_audit(world_vertices, surface_bvhs)
    symmetry = symmetry_audit(world_vertices)
    floating = floating_component_audit(world_vertices, master_vertices)
    pivots = pivot_audit(export_objects)
    materials = sorted(
        {
            slot.material.name
            for obj in export_objects
            if obj.type == "MESH"
            for slot in obj.material_slots
            if slot.material is not None
        }
    )
    build_metrics = read_json(cfg.LOG_DIR / "iteration_5_build.json")
    silhouette_metrics = read_json(cfg.LOG_DIR / "silhouette_metrics_final.json")
    reference_metrics = read_json(cfg.MEASUREMENTS_PATH)
    visual_iterations = read_json(cfg.LOG_DIR / "visual_iteration_log.json")
    command_history = read_json(cfg.LOG_DIR / "command_history.json")

    export_glb(export_objects, root)
    glb_size = cfg.GLB_PATH.stat().st_size
    reimport = clean_reimport()

    elapsed = time.perf_counter() - started
    report = {
        "project": "AEGIS-R7 graybox v3",
        "git": {
            "baseline_commit": cfg.BASELINE_COMMIT,
            "baseline_subject": git_value("show", "-s", "--format=%s", cfg.BASELINE_COMMIT),
            "branch": git_value("branch", "--show-current"),
        },
        "blender": {
            "executable": bpy.app.binary_path,
            "version": bpy.app.version_string,
            "version_tuple": list(bpy.app.version),
            "build_hash": bpy.app.build_hash.decode("utf-8"),
        },
        "host": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "actual_commands": command_history.get("commands", []),
        "reference_preprocessing": {
            "measurements_path": str(cfg.MEASUREMENTS_PATH),
            "derived_reference_directory": str(cfg.DERIVED_REFERENCE_DIR),
            "view_count": len(reference_metrics.get("views", {})),
            "warnings": reference_metrics.get("warnings", []),
            "reference_empty_warnings": build_metrics.get("reference_warnings", []),
        },
        "silhouette_validation": silhouette_metrics,
        "helmet_geometry": geometry,
        "head_proxy_clearance": build_metrics.get("clearance", {}),
        "faceplate_curvature": build_metrics.get("faceplate_curvature", {}),
        "seam_validation": seams,
        "symmetry_validation": symmetry,
        "floating_component_validation": {
            "objects": floating,
            "floating_objects": [name for name, value in floating.items() if value["floating_over_20_mm"]],
        },
        "pivot_origin_validation": pivots,
        "nodes_and_materials": {
            "export_object_count": len(export_objects),
            "export_node_names": sorted(names),
            "required_node_names": list(cfg.REQUIRED_EXPORT_PARTS),
            "missing_required_nodes": missing_required,
            "material_count": len(materials),
            "material_names": materials,
            "configured_review_material_count": len(cfg.MATERIALS),
            "configured_review_material_names": sorted(cfg.MATERIALS),
        },
        "glb": {
            "path": str(cfg.GLB_PATH),
            "size_bytes": glb_size,
            "clean_reimport": reimport,
        },
        "review_outputs": review_manifest(),
        "visual_iterations": visual_iterations.get("iterations", []),
        "algorithm_change_from_v2": [
            "Measured front width and independent side depth profiles drive a multi-section superellipse loft instead of create_open_ellipsoid_shell().",
            "Visible armor is sampled from one HelmetEnvelope_Master or analytic parameter patches on that surface instead of create_ellipsoid_patch() and create_prism().",
            "Faceplate uses continuous surface parameters with variable brow, side wrap, lower taper, center bulge, thickness, and controlled subdivision; no fixed-Y plate is used.",
            "Eyes sample the faceplate curvature, with a surface-conforming housing and relatively inset lens.",
            "Jaw, cheek, crown, temple, and rear shell share the master cage; ear covers use an embedded stepped coaxial profile; the neck entry is a low rectangular elliptical ribbon rather than a torus.",
            "Every movable mesh has local vertices, an explicit mechanical origin, and a nonzero transform parented under HelmetRoot.",
        ],
        "known_issues": [
            "The source is an internally inconsistent AI concept sheet; the largest remaining side-contour error is concentrated near the bottom/neck silhouette, while mean side error remains within the acceptance gate.",
            "Rear-shell center-spine and internal mechanisms are deliberately simplified to major graybox divisions.",
            "The faceplate is a pre-production continuous surface and may show mild highlight undulation before production retopology; the silhouette and wrap are stable.",
            "No production UVs, textures, micro-fasteners, rig, animation, or Web/Face Landmarker integration are included.",
        ],
        "files": {
            "blend": str(cfg.BLEND_PATH),
            "blend_size_bytes": cfg.BLEND_PATH.stat().st_size,
            "glb": str(cfg.GLB_PATH),
            "report": str(cfg.REPORT_PATH),
        },
        "export_and_validation_seconds": round(elapsed, 4),
    }
    cfg.REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (cfg.LOG_DIR / "export_reimport_validation.json").write_text(
        json.dumps(
            {
                "script": str(Path(__file__).resolve()),
                "elapsed_seconds": round(elapsed, 4),
                "glb_path": str(cfg.GLB_PATH),
                "glb_size_bytes": glb_size,
                "reimport": reimport,
                "geometry_totals": geometry["totals"],
                "triangle_count": geometry["evaluated_triangle_count"],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(
        f"AEGIS_V3_EXPORT_OK nodes={len(export_objects)} "
        f"triangles={geometry['evaluated_triangle_count']} reimport={reimport['success']}"
    )
    print(f"AEGIS_V3_GLB={cfg.GLB_PATH}")
    print(f"AEGIS_V3_REPORT={cfg.REPORT_PATH}")


if __name__ == "__main__":
    main()
