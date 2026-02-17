"""Vehicle LOD Generator for Sollumz.

Automatically generates multiple LOD levels for vehicle fragments (YFT) with
intelligent, component-aware decimation. Builds on the existing Sollumz LOD system
and Blender's decimate modifier.
"""
import bpy
from bpy.types import Context, Object, Operator, Panel, PropertyGroup
from bpy.props import IntProperty

from ..lods import LODLevels
from ..sollumz_properties import LODLevel, SollumType, SOLLUMZ_UI_NAMES
from ..sollumz_helper import find_sollumz_parent
from ..tools.blenderhelper import get_children_recursive


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

# Keywords used to classify vehicle sub-meshes by name.
_UNDERBODY_KEYWORDS = {
    "chassis_under", "underbody", "engine", "exhaust", "suspension",
    "axle", "driveshaft", "transmission", "oil_pan",
}

_DETAIL_KEYWORDS = {
    "mirror", "antenna", "handle", "badge", "emblem",
    "screw", "bolt", "wiper", "trim", "moulding",
    "grill", "grille", "vent",
}

_GLASS_KEYWORDS = {
    "window", "windshield", "windscreen", "glass",
}

_WHEEL_KEYWORDS = {
    "wheel",
}

_LIGHT_KEYWORDS = {
    "headlight", "taillight", "brakelight", "indicator",
    "light", "lamp", "fog",
}


def _classify_mesh(obj: Object) -> str:
    """Return a classification string for *obj* based on its name.

    Categories:
        ``"underbody"`` – parts under the car (aggressive reduction)
        ``"detail"``    – small detail parts (moderate reduction)
        ``"glass"``     – windows / glass (preserve shape, reduce moderately)
        ``"wheel"``     – wheels (preserve outline)
        ``"light"``     – lights (moderate reduction)
        ``"body"``      – main body panels (preserve carefully)
    """
    name_lower = obj.name.lower()

    for kw in _UNDERBODY_KEYWORDS:
        if kw in name_lower:
            return "underbody"
    for kw in _WHEEL_KEYWORDS:
        if kw in name_lower:
            return "wheel"
    for kw in _GLASS_KEYWORDS:
        if kw in name_lower:
            return "glass"
    for kw in _LIGHT_KEYWORDS:
        if kw in name_lower:
            return "light"
    for kw in _DETAIL_KEYWORDS:
        if kw in name_lower:
            return "detail"

    return "body"


# Per-category decimation ratio *multipliers* applied on top of the base ratio
# for each LOD level.  A higher multiplier means *more* geometry is removed.
# The value is multiplied with the base decimation ratio to get the final ratio
# passed to the decimate modifier (``ratio = 1.0 - base * multiplier``).
_CATEGORY_AGGRESSION: dict[str, float] = {
    "body":      1.0,
    "glass":     1.0,
    "wheel":     1.0,
    "light":     1.1,
    "detail":    1.3,
    "underbody": 1.5,
}

# Maximum fraction of geometry to remove in a single decimation pass.
# Capped below 1.0 to avoid producing degenerate meshes.
_MAX_DECIMATION_RATIO = 0.99

# Minimum fraction of geometry to keep after decimation, ensuring the mesh
# is never fully collapsed.
_MIN_KEEP_RATIO = 0.01

# Default target polygon counts per LOD level (informational, used to compute
# an appropriate decimation ratio when the user enables adaptive mode).
_DEFAULT_TARGETS: dict[LODLevel, int] = {
    LODLevel.HIGH:    200_000,
    LODLevel.MEDIUM:  90_000,
    LODLevel.LOW:     40_000,
    LODLevel.VERYLOW: 7_500,
}


def _get_total_poly_count(fragment_obj: Object) -> int:
    """Return total polygon count across all DRAWABLE_MODEL children."""
    total = 0
    for child in get_children_recursive(fragment_obj):
        if child.sollum_type != SollumType.DRAWABLE_MODEL or child.type != "MESH":
            continue
        lod = child.sz_lods.active_lod
        mesh = lod.mesh
        if mesh is not None:
            total += len(mesh.polygons)
    return total


def _get_model_objects(fragment_obj: Object) -> list[Object]:
    """Return all DRAWABLE_MODEL mesh children of *fragment_obj*."""
    models: list[Object] = []
    for child in get_children_recursive(fragment_obj):
        if child.sollum_type == SollumType.DRAWABLE_MODEL and child.type == "MESH":
            models.append(child)
    return models


def _compute_base_ratio_for_lod(
    current_polys: int,
    target_polys: int,
) -> float:
    """Compute a base decimation ratio to reach *target_polys* from *current_polys*.

    Returns a value in [0.0, 1.0] where 0.0 means keep everything and
    1.0 means remove everything.
    """
    if current_polys <= 0 or current_polys <= target_polys:
        return 0.0
    ratio = 1.0 - (target_polys / current_polys)
    return max(0.0, min(ratio, _MAX_DECIMATION_RATIO))


def _decimate_ratio_for_mesh(
    base_ratio: float,
    category: str,
) -> float:
    """Return the *keep* ratio (passed to the decimate modifier) for a mesh.

    ``base_ratio`` is the fraction to *remove* (0 = keep all, 1 = remove all).
    The function applies the category aggression multiplier and clamps the result.
    """
    aggression = _CATEGORY_AGGRESSION.get(category, 1.0)
    remove = base_ratio * aggression
    keep = max(_MIN_KEEP_RATIO, min(1.0 - remove, 1.0))
    return keep


# ---------------------------------------------------------------------------
#  Operators
# ---------------------------------------------------------------------------

class SOLLUMZ_OT_vehicle_generate_lods(Operator):
    """Generate LOD levels for all drawable models in the active Fragment with component-aware decimation"""
    bl_idname = "sollumz.vehicle_generate_lods"
    bl_label = "Generate Vehicle LODs"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Context) -> bool:
        obj = context.active_object
        if obj is None:
            return False
        frag = find_sollumz_parent(obj, SollumType.FRAGMENT)
        return frag is not None

    def execute(self, context: Context) -> set[str]:
        obj = context.active_object
        frag_obj = find_sollumz_parent(obj, SollumType.FRAGMENT)
        if frag_obj is None:
            self.report({"ERROR"}, "No Fragment object found")
            return {"CANCELLED"}

        props = context.scene.sz_vehicle_lod_props

        models = _get_model_objects(frag_obj)
        if not models:
            self.report({"WARNING"}, "No drawable model meshes found in Fragment")
            return {"CANCELLED"}

        lod_levels = [
            (LODLevel.HIGH, props.target_high),
            (LODLevel.MEDIUM, props.target_medium),
            (LODLevel.LOW, props.target_low),
            (LODLevel.VERYLOW, props.target_verylow),
        ]

        total_before = _get_total_poly_count(frag_obj)
        generated_count = 0

        for lod_level, target in lod_levels:
            # Compute base decimation ratio from total current polys
            base_ratio = _compute_base_ratio_for_lod(total_before, target)
            if base_ratio <= 0.0:
                # Current poly count is already below target, copy mesh as-is
                for model_obj in models:
                    self._copy_active_lod(model_obj, lod_level)
                generated_count += 1
                continue

            for model_obj in models:
                self._generate_lod_for_model(
                    context, model_obj, lod_level, base_ratio,
                )

            generated_count += 1

        # Restore all models to their original LOD level
        for model_obj in models:
            if model_obj.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")

        total_after: dict[str, int] = {}
        for lod_level, _ in lod_levels:
            count = 0
            for model_obj in models:
                lod = model_obj.sz_lods.get_lod(lod_level)
                m = lod.mesh
                if m is not None:
                    count += len(m.polygons)
            total_after[SOLLUMZ_UI_NAMES[lod_level]] = count

        report_lines = [f"Generated {generated_count} LOD level(s) for '{frag_obj.name}'"]
        report_lines.append(f"  Original: {total_before:,} polys")
        for name, count in total_after.items():
            report_lines.append(f"  {name}: {count:,} polys")
        self.report({"INFO"}, " | ".join(report_lines))

        return {"FINISHED"}

    # -- internal helpers --------------------------------------------------

    @staticmethod
    def _copy_active_lod(model_obj: Object, lod_level: LODLevel) -> None:
        """Copy the currently active LOD mesh into *lod_level*."""
        lods: LODLevels = model_obj.sz_lods
        src_mesh = lods.active_lod.mesh
        if src_mesh is None:
            return
        lod = lods.get_lod(lod_level)
        if lod.mesh is not None:
            return  # already has a mesh
        lod.mesh = src_mesh.copy()

    @staticmethod
    def _generate_lod_for_model(
        context: Context,
        model_obj: Object,
        lod_level: LODLevel,
        base_ratio: float,
    ) -> None:
        """Generate a single LOD mesh for *model_obj* via decimation."""
        lods: LODLevels = model_obj.sz_lods
        lod = lods.get_lod(lod_level)

        if lod.mesh is not None:
            return  # already has a mesh, don't overwrite

        src_mesh = lods.active_lod.mesh
        if src_mesh is None:
            return

        category = _classify_mesh(model_obj)
        keep_ratio = _decimate_ratio_for_mesh(base_ratio, category)

        new_mesh = src_mesh.copy()
        new_mesh.name = f"{model_obj.name}.{SOLLUMZ_UI_NAMES[lod_level].lower()}"

        # Store the mesh in the LOD slot and switch to it to apply decimation
        lod.mesh = new_mesh

        prev_lod_level = lods.active_lod_level
        prev_active = context.view_layer.objects.active

        context.view_layer.objects.active = model_obj
        if model_obj.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        lods.active_lod_level = lod_level

        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.decimate(ratio=keep_ratio)
        bpy.ops.object.mode_set(mode="OBJECT")

        lods.active_lod_level = prev_lod_level
        context.view_layer.objects.active = prev_active


class SOLLUMZ_OT_vehicle_batch_generate_lods(Operator):
    """Generate LOD levels for ALL Fragment objects in the scene"""
    bl_idname = "sollumz.vehicle_batch_generate_lods"
    bl_label = "Batch Generate Vehicle LODs"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Context) -> bool:
        return any(
            obj.sollum_type == SollumType.FRAGMENT
            for obj in context.scene.objects
        )

    def execute(self, context: Context) -> set[str]:
        fragments = [
            obj for obj in context.scene.objects
            if obj.sollum_type == SollumType.FRAGMENT
        ]

        if not fragments:
            self.report({"WARNING"}, "No Fragment objects found in scene")
            return {"CANCELLED"}

        processed = 0
        for frag_obj in fragments:
            context.view_layer.objects.active = frag_obj
            result = bpy.ops.sollumz.vehicle_generate_lods()
            if "FINISHED" in result:
                processed += 1

        self.report({"INFO"}, f"Batch LOD generation complete: {processed}/{len(fragments)} fragments processed")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
#  Properties
# ---------------------------------------------------------------------------

class SzVehicleLodProperties(PropertyGroup):
    """Configuration properties for vehicle LOD generation."""

    target_high: IntProperty(
        name="High LOD Target",
        description="Target polygon count for High LOD (LOD 0). Typically used at close range",
        default=200_000,
        min=1_000,
        max=2_000_000,
    )
    target_medium: IntProperty(
        name="Medium LOD Target",
        description="Target polygon count for Medium LOD (LOD 1). Typically used at medium range",
        default=90_000,
        min=500,
        max=1_000_000,
    )
    target_low: IntProperty(
        name="Low LOD Target",
        description="Target polygon count for Low LOD (LOD 2). Typically used at far range",
        default=40_000,
        min=100,
        max=500_000,
    )
    target_verylow: IntProperty(
        name="Very Low LOD Target",
        description="Target polygon count for Very Low LOD (LOD 3). Typically used at very far range",
        default=7_500,
        min=50,
        max=100_000,
    )


# ---------------------------------------------------------------------------
#  UI Panel
# ---------------------------------------------------------------------------

class SOLLUMZ_PT_VEHICLE_LOD_GENERATOR_PANEL(Panel):
    bl_label = "Vehicle LOD Generator"
    bl_idname = "SOLLUMZ_PT_VEHICLE_LOD_GENERATOR_PANEL"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Sollumz Tools"
    bl_options = {"DEFAULT_CLOSED"}
    bl_parent_id = "SOLLUMZ_PT_FRAGMENT_TOOL_PANEL"

    bl_order = 5

    def draw_header(self, context: Context):
        self.layout.label(text="", icon="MOD_DECIM")

    def draw(self, context: Context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        props = context.scene.sz_vehicle_lod_props

        col = layout.column(align=True)
        col.prop(props, "target_high")
        col.prop(props, "target_medium")
        col.prop(props, "target_low")
        col.prop(props, "target_verylow")

        layout.separator()

        col = layout.column()
        col.operator(
            SOLLUMZ_OT_vehicle_generate_lods.bl_idname,
            icon="MOD_DECIM",
        )
        col.operator(
            SOLLUMZ_OT_vehicle_batch_generate_lods.bl_idname,
            icon="FILE_REFRESH",
        )


# ---------------------------------------------------------------------------
#  Registration
# ---------------------------------------------------------------------------

def register():
    bpy.types.Scene.sz_vehicle_lod_props = bpy.props.PointerProperty(
        type=SzVehicleLodProperties
    )


def unregister():
    del bpy.types.Scene.sz_vehicle_lod_props
