"""Create and render a minimal Blender preflight scene.

This script is intentionally deterministic and safe to run repeatedly in Blender's
background mode. Outputs are resolved relative to this file, not the shell's cwd.
"""

from pathlib import Path

import bpy
from mathutils import Vector


BLENDER_DIR = Path(__file__).resolve().parent.parent
RENDER_PATH = BLENDER_DIR / "output" / "preflight" / "preflight.png"
BLEND_PATH = BLENDER_DIR / "work" / "preflight.blend"


def look_at(obj: bpy.types.Object, target: tuple[float, float, float]) -> None:
    """Point an object's local -Z axis toward target while keeping local Y up."""
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def choose_eevee_engine(scene: bpy.types.Scene) -> str:
    """Select the Eevee identifier exposed by the current Blender build."""
    engine_property = bpy.types.RenderSettings.bl_rna.properties["engine"]
    supported = {item.identifier for item in engine_property.enum_items}
    for candidate in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        if candidate in supported:
            scene.render.engine = candidate
            return candidate
    raise RuntimeError(
        "This Blender build exposes no supported Eevee render engine. "
        f"Available engines: {sorted(supported)}"
    )


def make_material(
    name: str, color: tuple[float, float, float, float], roughness: float
) -> bpy.types.Material:
    material = bpy.data.materials.new(name=name)
    material.diffuse_color = color
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    principled = nodes.new(type="ShaderNodeBsdfPrincipled")
    principled.name = "Principled BSDF"
    output = nodes.new(type="ShaderNodeOutputMaterial")
    output.name = "Material Output"
    material.node_tree.links.new(principled.outputs["BSDF"], output.inputs["Surface"])
    principled.inputs["Base Color"].default_value = color
    principled.inputs["Roughness"].default_value = roughness
    principled.inputs["Metallic"].default_value = 0.0
    return material


def add_area_light(
    name: str,
    location: tuple[float, float, float],
    energy: float,
    size: float,
) -> bpy.types.Object:
    light_data = bpy.data.lights.new(name=name, type="AREA")
    light_data.energy = energy
    light_data.shape = "DISK"
    light_data.size = size
    light = bpy.data.objects.new(name=name, object_data=light_data)
    bpy.context.scene.collection.objects.link(light)
    light.location = location
    look_at(light, (0.0, 0.0, 0.1))
    return light


def main() -> None:
    RENDER_PATH.parent.mkdir(parents=True, exist_ok=True)
    BLEND_PATH.parent.mkdir(parents=True, exist_ok=True)

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for datablocks in (
        bpy.data.meshes,
        bpy.data.curves,
        bpy.data.cameras,
        bpy.data.lights,
        bpy.data.materials,
    ):
        for datablock in list(datablocks):
            if datablock.users == 0:
                datablocks.remove(datablock)

    scene = bpy.context.scene
    engine = choose_eevee_engine(scene)

    neutral_gray = make_material(
        "Preflight Neutral Gray", (0.34, 0.36, 0.39, 1.0), roughness=0.42
    )
    ground_gray = make_material(
        "Preflight Ground Gray", (0.22, 0.23, 0.25, 1.0), roughness=0.58
    )

    bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0.0, 0.0, 0.0))
    cube = bpy.context.object
    cube.name = "Preflight Beveled Cube"
    cube.data.materials.append(neutral_gray)
    bevel = cube.modifiers.new(name="Rounded Edges", type="BEVEL")
    bevel.width = 0.18
    bevel.segments = 6

    bpy.ops.mesh.primitive_plane_add(size=20.0, location=(0.0, 0.0, -1.08))
    ground = bpy.context.object
    ground.name = "Preflight Ground"
    ground.data.materials.append(ground_gray)

    camera_data = bpy.data.cameras.new(name="Preflight Camera")
    camera = bpy.data.objects.new(name="Preflight Camera", object_data=camera_data)
    scene.collection.objects.link(camera)
    camera.location = (4.6, -5.2, 3.6)
    camera.data.lens = 52.0
    look_at(camera, (0.0, 0.0, 0.0))
    scene.camera = camera

    add_area_light("Key Light", (4.0, -4.0, 5.5), energy=900.0, size=4.0)
    add_area_light("Fill Light", (-4.5, -1.5, 2.8), energy=500.0, size=5.0)
    add_area_light("Rim Light", (2.5, 4.0, 4.5), energy=750.0, size=3.0)

    world = scene.world or bpy.data.worlds.new("Preflight World")
    scene.world = world
    world.use_nodes = True
    world_nodes = world.node_tree.nodes
    world_nodes.clear()
    background = world_nodes.new(type="ShaderNodeBackground")
    background.name = "Background"
    world_output = world_nodes.new(type="ShaderNodeOutputWorld")
    world_output.name = "World Output"
    world.node_tree.links.new(background.outputs["Background"], world_output.inputs["Surface"])
    background.inputs["Color"].default_value = (0.18, 0.20, 0.23, 1.0)
    background.inputs["Strength"].default_value = 0.35

    scene.render.resolution_x = 512
    scene.render.resolution_y = 512
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.film_transparent = False
    scene.render.filepath = str(RENDER_PATH)
    bpy.context.preferences.filepaths.save_version = 0

    scene.render.image_settings.color_depth = "8"
    if hasattr(scene, "view_settings"):
        scene.view_settings.exposure = 0.0

    bpy.ops.render.render(write_still=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(BLEND_PATH))

    print(f"PREFLIGHT_ENGINE={engine}")
    print(f"PREFLIGHT_RENDER={RENDER_PATH}")
    print(f"PREFLIGHT_BLEND={BLEND_PATH}")


if __name__ == "__main__":
    main()
