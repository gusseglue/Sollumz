"""Vehicle LOD Generator for Sollumz.

Automatically generates multiple LOD levels for vehicle fragments (YFT) with
intelligent, component-aware decimation.  Uses Blender's Decimate modifier
applied to temporary objects to reliably reduce polygon count while preserving
the LOD system's mesh management.
"""
import bpy
from bpy.types import Context, Mesh, Object, Operator, Panel, PropertyGroup
from bpy.props import IntProperty

from ..lods import LODLevels
from ..sollumz_properties import LODLevel, SollumType, SOLLUMZ_UI_NAMES
from ..sollumz_helper import find_sollumz_parent
from ..tools.blenderhelper import get_children_recursive


# ---------------------------------------------------------------------------
#  Helpers – mesh classification
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

_INTERIOR_KEYWORDS = {
    "interior", "dashboard", "dash", "steering", "steeringwheel",
    "seat", "console", "dial", "gauge", "pedal", "airbag",
    "glovebox", "handbrake", "shifter", "gear",
}


def _classify_mesh(obj: Object) -> str:
    """Return a classification string for *obj* based on its name.

    Categories:
        ``"interior"``  – interior parts (very careful reduction)
        ``"underbody"`` – parts under the car (aggressive reduction)
        ``"detail"``    – small detail parts (moderate reduction)
        ``"glass"``     – windows / glass (preserve shape, reduce moderately)
        ``"wheel"``     – wheels (preserve outline)
        ``"light"``     – lights (moderate reduction)
        ``"body"``      – main body panels (preserve carefully)
    """
    name_lower = obj.name.lower()

    for kw in _INTERIOR_KEYWORDS:
        if kw in name_lower:
            return "interior"
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


# ---------------------------------------------------------------------------
#  Helpers – decimation parameters
# ---------------------------------------------------------------------------

# Per-category decimation ratio *multipliers*.  Lower values are more
# *protective* (less geometry removed); higher values are more aggressive.
#
# Vehicle meshes are often *not* merged — vertices at shared edges between
# parts don't coincide.  Categories with typically non-merged geometry
# (wheels, body shell) use lower multipliers to preserve their shape.
_CATEGORY_AGGRESSION: dict[str, float] = {
    "interior":  0.3,
    "wheel":     0.4,
    "body":      0.7,
    "glass":     0.8,
    "light":     0.9,
    "detail":    1.3,
    "underbody": 1.5,
}

_MAX_DECIMATION_RATIO = 0.99
_MIN_KEEP_RATIO = 0.05

_DEFAULT_TARGETS: dict[LODLevel, int] = {
    LODLevel.VERYHIGH: 200_000,
    LODLevel.HIGH:     175_000,
    LODLevel.MEDIUM:   90_000,
    LODLevel.LOW:      40_000,
    LODLevel.VERYLOW:  7_500,
}


def _compute_base_ratio_for_lod(current_polys: int, target_polys: int) -> float:
    """Fraction of geometry to *remove* to reach *target_polys* from *current_polys*."""
    if current_polys <= 0 or current_polys <= target_polys:
        return 0.0
    ratio = 1.0 - (target_polys / current_polys)
    return max(0.0, min(ratio, _MAX_DECIMATION_RATIO))


def _keep_ratio_for_mesh(base_ratio: float, category: str) -> float:
    """Fraction of geometry to *keep* after applying category aggression."""
    aggression = _CATEGORY_AGGRESSION.get(category, 1.0)
    remove = base_ratio * aggression
    return max(_MIN_KEEP_RATIO, min(1.0 - remove, 1.0))


# ---------------------------------------------------------------------------
#  Helpers – model object queries
# ---------------------------------------------------------------------------

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


def _find_original_mesh(model_obj: Object) -> Mesh | None:
    """Return the highest-quality mesh available for *model_obj*.

    Checks LOD levels from highest to lowest and returns the first mesh
    found.  This is the mesh that will be used as the source for all
    generated LOD levels.
    """
    lods: LODLevels = model_obj.sz_lods
    for lod_level in LODLevel:
        lod = lods.get_lod(lod_level)
        m = lod.mesh
        if m is not None:
            return m
    # Fallback: the object's current data block
    if model_obj.type == "MESH":
        return model_obj.data
    return None


# ---------------------------------------------------------------------------
#  Decimation via Blender's Decimate modifier on a temporary object
# ---------------------------------------------------------------------------

def _decimate_mesh(src_mesh: Mesh, keep_ratio: float) -> Mesh:
    """Create a decimated copy of *src_mesh* using Blender's Decimate modifier.

    Only the Decimate modifier (COLLAPSE mode) is applied.  No vertex welding
    or normal recalculation is performed because vehicle meshes rely on split
    normals (separate vertices at the same position) to define hard/sharp
    edges.  Welding those vertices or overriding the normals makes the model
    look soft and round and causes shadow artefacts in GTA V.

    Returns a new Mesh data-block.  The original is not modified.
    """
    new_mesh = src_mesh.copy()

    # Create a temporary object to host the modifier
    temp_obj = bpy.data.objects.new("_sz_lod_tmp", new_mesh)
    bpy.context.collection.objects.link(temp_obj)

    try:
        bpy.context.view_layer.objects.active = temp_obj
        temp_obj.select_set(True)

        # Decimate — the only modifier we apply.
        mod_dec = temp_obj.modifiers.new(name="_decimate", type="DECIMATE")
        mod_dec.decimate_type = "COLLAPSE"
        mod_dec.ratio = keep_ratio
        bpy.ops.object.modifier_apply(modifier=mod_dec.name)

        result_mesh = temp_obj.data
    finally:
        bpy.data.objects.remove(temp_obj, do_unlink=True)

    return result_mesh


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
            (LODLevel.VERYHIGH, props.target_veryhigh),
            (LODLevel.HIGH, props.target_high),
            (LODLevel.MEDIUM, props.target_medium),
            (LODLevel.LOW, props.target_low),
            (LODLevel.VERYLOW, props.target_verylow),
        ]

        # Find the original mesh for each model (highest quality available)
        original_meshes: dict[str, Mesh] = {}
        original_total = 0
        for model_obj in models:
            src = _find_original_mesh(model_obj)
            if src is not None:
                original_meshes[model_obj.name] = src
                original_total += len(src.polygons)

        if original_total == 0:
            self.report({"WARNING"}, "No mesh data found in any drawable model")
            return {"CANCELLED"}

        generated_count = 0

        # Save and restore active object after all LOD generation
        prev_active = context.view_layer.objects.active

        try:
            for lod_level, target in lod_levels:
                base_ratio = _compute_base_ratio_for_lod(original_total, target)

                for model_obj in models:
                    src_mesh = original_meshes.get(model_obj.name)
                    if src_mesh is None:
                        continue

                    if base_ratio <= 0.0:
                        # Target is above current poly count — copy original as-is
                        self._set_lod_mesh(model_obj, lod_level, src_mesh.copy())
                    else:
                        category = _classify_mesh(model_obj)
                        if category == "glass":
                            # Glass must never be decimated — it creates holes
                            self._set_lod_mesh(model_obj, lod_level, src_mesh.copy())
                            continue
                        keep_ratio = _keep_ratio_for_mesh(base_ratio, category)
                        decimated = _decimate_mesh(src_mesh, keep_ratio)
                        decimated.name = f"{model_obj.name}.{SOLLUMZ_UI_NAMES[lod_level].lower()}"
                        self._set_lod_mesh(model_obj, lod_level, decimated)

                generated_count += 1
        finally:
            # Restore active object
            context.view_layer.objects.active = prev_active

        # Build report
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
        report_lines.append(f"  Original: {original_total:,} polys")
        for name, count in total_after.items():
            report_lines.append(f"  {name}: {count:,} polys")
        self.report({"INFO"}, " | ".join(report_lines))

        return {"FINISHED"}

    # -- internal helpers --------------------------------------------------

    @staticmethod
    def _set_lod_mesh(model_obj: Object, lod_level: LODLevel, mesh: Mesh) -> None:
        """Set the mesh for *lod_level*, replacing any existing mesh."""
        lods: LODLevels = model_obj.sz_lods
        lod = lods.get_lod(lod_level)
        # Always overwrite — the user asked to (re)generate
        lod.mesh = mesh


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

    target_veryhigh: IntProperty(
        name="Very High LOD Target",
        description="Target polygon count for Very High LOD. Typically the closest/highest detail level",
        default=200_000,
        min=1_000,
        max=2_000_000,
    )
    target_high: IntProperty(
        name="High LOD Target",
        description="Target polygon count for High LOD (LOD 0). Typically used at close range",
        default=175_000,
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
        col.prop(props, "target_veryhigh")
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
