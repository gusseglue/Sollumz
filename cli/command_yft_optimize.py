"""CLI command for batch optimizing YFT files with automatic LOD generation."""
from pathlib import Path

CMD_ID = "sz_yft_optimize"


def main(argv: list[str]) -> int:
    import sys
    import os
    from argparse import ArgumentParser
    import bpy

    parser = ArgumentParser(
        prog=os.path.basename(sys.argv[0]) + " --command " + CMD_ID,
        description="Optimize YFT files by automatically generating LOD levels with polygon reduction.",
    )
    parser.add_argument(
        "files",
        nargs="+",
        type=Path,
        help="Input YFT files to optimize",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        help="Output directory for optimized YFT files. If not specified, files are saved in-place.",
        required=False,
    )
    parser.add_argument(
        "--lod-high",
        type=int,
        default=200000,
        help="Target polygon count for LOD High (default: 200000)",
    )
    parser.add_argument(
        "--lod-medium",
        type=int,
        default=100000,
        help="Target polygon count for LOD Medium (default: 100000)",
    )
    parser.add_argument(
        "--lod-low",
        type=int,
        default=50000,
        help="Target polygon count for LOD Low (default: 50000)",
    )
    parser.add_argument(
        "--lod-verylow",
        type=int,
        default=10000,
        help="Target polygon count for LOD Very Low (default: 10000)",
    )
    parser.add_argument(
        "--preserve-uvs",
        action="store_true",
        default=True,
        help="Preserve UV maps during decimation (default: True)",
    )
    parser.add_argument(
        "--preserve-vertex-colors",
        action="store_true",
        default=True,
        help="Preserve vertex colors during decimation (default: True)",
    )
    parser.add_argument(
        "--use-symmetry",
        action="store_true",
        default=True,
        help="Use symmetrical decimation (default: True)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Output file for optimization report",
        required=False,
    )

    args = parser.parse_args(argv)

    # Prepare output directory
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)

    report_lines = []
    report_lines.append("=" * 80)
    report_lines.append("YFT Batch Optimization Report")
    report_lines.append("=" * 80)
    report_lines.append("")

    total_files = len(args.files)
    processed_files = 0
    failed_files = []

    for file_path in args.files:
        if not file_path.exists():
            print(f"Error: File not found: {file_path}")
            failed_files.append((file_path, "File not found"))
            continue

        try:
            print(f"\nProcessing: {file_path.name}")
            report_lines.append(f"\nFile: {file_path.name}")
            report_lines.append("-" * 80)

            # Clear scene and import YFT
            bpy.ops.wm.read_homefile()
            bpy.ops.sollumz.import_assets(
                directory=str(file_path.parent.absolute()),
                files=[{"name": file_path.name}],
            )

            # Find the fragment object
            frag_obj = None
            for obj in bpy.data.objects:
                if obj.sollum_type == "sollumz_fragment":
                    frag_obj = obj
                    break

            if frag_obj is None:
                print(f"  Warning: No fragment object found in {file_path.name}")
                failed_files.append((file_path, "No fragment object found"))
                report_lines.append("  ERROR: No fragment object found")
                continue

            # Select the fragment object
            bpy.context.view_layer.objects.active = frag_obj
            frag_obj.select_set(True)

            # Run optimization
            result = optimize_yft(
                frag_obj,
                args.lod_high,
                args.lod_medium,
                args.lod_low,
                args.lod_verylow,
                args.preserve_uvs,
                args.preserve_vertex_colors,
                args.use_symmetry,
                report_lines
            )

            if result:
                # Export optimized YFT
                if args.output_dir:
                    output_path = args.output_dir / file_path.name
                else:
                    output_path = file_path

                bpy.ops.object.select_all(action='DESELECT')
                frag_obj.select_set(True)
                bpy.ops.sollumz.export_assets(
                    directory=str(output_path.parent.absolute()),
                )

                print(f"  Success: Saved to {output_path}")
                report_lines.append(f"  Saved to: {output_path}")
                processed_files += 1
            else:
                failed_files.append((file_path, "Optimization failed"))
                report_lines.append("  ERROR: Optimization failed")

        except Exception as e:
            print(f"  Error processing {file_path.name}: {str(e)}")
            failed_files.append((file_path, str(e)))
            report_lines.append(f"  ERROR: {str(e)}")

    # Summary
    report_lines.append("")
    report_lines.append("=" * 80)
    report_lines.append("Summary")
    report_lines.append("=" * 80)
    report_lines.append(f"Total files: {total_files}")
    report_lines.append(f"Successfully processed: {processed_files}")
    report_lines.append(f"Failed: {len(failed_files)}")

    if failed_files:
        report_lines.append("")
        report_lines.append("Failed files:")
        for file_path, reason in failed_files:
            report_lines.append(f"  - {file_path.name}: {reason}")

    # Print report
    print("\n")
    for line in report_lines:
        print(line)

    # Save report if requested
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("w") as f:
            f.write("\n".join(report_lines))
        print(f"\nReport saved to: {args.report}")

    return 0 if len(failed_files) == 0 else 1


def optimize_yft(frag_obj, lod_high, lod_medium, lod_low, lod_verylow,
                 preserve_uvs, preserve_vertex_colors, use_symmetry, report_lines):
    """Optimize a single YFT fragment by generating LOD levels."""
    import bpy
    from ..sollumz_properties import SollumType, LODLevel

    # Find the drawable object
    drawable_obj = None
    for child in frag_obj.children:
        if child.sollum_type == SollumType.DRAWABLE:
            drawable_obj = child
            break

    if drawable_obj is None:
        report_lines.append("  ERROR: No Drawable object found")
        return False

    # Process all drawable models
    model_objs = [child for child in drawable_obj.children
                  if child.sollum_type == SollumType.DRAWABLE_MODEL]

    if not model_objs:
        report_lines.append("  ERROR: No Drawable Models found")
        return False

    report_lines.append(f"  Models found: {len(model_objs)}")

    total_original_polys = 0
    total_optimized_polys = {
        "high": 0,
        "medium": 0,
        "low": 0,
        "verylow": 0
    }

    for model_obj in model_objs:
        result = optimize_model_lods(
            model_obj,
            lod_high,
            lod_medium,
            lod_low,
            lod_verylow,
            preserve_uvs,
            preserve_vertex_colors,
            use_symmetry,
            report_lines
        )
        if result:
            total_original_polys += result["original"]
            for lod_name in ["high", "medium", "low", "verylow"]:
                total_optimized_polys[lod_name] += result[lod_name]

    report_lines.append("")
    report_lines.append("  Totals:")
    report_lines.append(f"    Original: {total_original_polys:,} polygons")
    report_lines.append(f"    LOD High: {total_optimized_polys['high']:,} polygons")
    report_lines.append(f"    LOD Medium: {total_optimized_polys['medium']:,} polygons")
    report_lines.append(f"    LOD Low: {total_optimized_polys['low']:,} polygons")
    report_lines.append(f"    LOD Very Low: {total_optimized_polys['verylow']:,} polygons")

    return True


def optimize_model_lods(model_obj, lod_high, lod_medium, lod_low, lod_verylow,
                        preserve_uvs, preserve_vertex_colors, use_symmetry, report_lines):
    """Optimize a single drawable model by generating LOD levels."""
    import bpy
    from ..sollumz_properties import LODLevel

    if model_obj.type != "MESH":
        return None

    lods = model_obj.sz_lods

    # Get the highest existing LOD as source
    source_lod = None
    source_mesh = None
    for lod_level in [LODLevel.VERYHIGH, LODLevel.HIGH, LODLevel.MEDIUM, LODLevel.LOW]:
        lod = lods.get_lod(lod_level)
        if lod.mesh is not None:
            source_lod = lod_level
            source_mesh = lod.mesh
            break

    if source_mesh is None:
        report_lines.append(f"    {model_obj.name}: Skipped (no source mesh)")
        return None

    # Count original polygons
    original_poly_count = len(source_mesh.polygons)
    report_lines.append(f"    {model_obj.name}:")
    report_lines.append(f"      Original: {original_poly_count:,} polygons")

    result = {
        "original": original_poly_count,
        "high": 0,
        "medium": 0,
        "low": 0,
        "verylow": 0
    }

    # Define LOD levels to generate
    lod_targets = [
        (LODLevel.HIGH, lod_high, "high"),
        (LODLevel.MEDIUM, lod_medium, "medium"),
        (LODLevel.LOW, lod_low, "low"),
        (LODLevel.VERYLOW, lod_verylow, "verylow"),
    ]

    previous_mode = model_obj.mode
    previous_lod_level = lods.active_lod_level

    for lod_level, target_count, lod_name in lod_targets:
        # Calculate decimation ratio
        ratio = min(1.0, target_count / original_poly_count) if original_poly_count > 0 else 1.0

        # Create a copy of the source mesh
        new_mesh = source_mesh.copy()
        new_mesh.name = f"{model_obj.name}.{lod_name}"

        # Set the mesh for this LOD
        lods.get_lod(lod_level).mesh = new_mesh

        # Switch to this LOD to edit it
        bpy.ops.object.mode_set(mode="OBJECT")
        lods.active_lod_level = lod_level

        # Apply decimation
        if ratio < 1.0:
            # Add decimate modifier
            decimate_mod = model_obj.modifiers.new(name="TempDecimate", type="DECIMATE")
            decimate_mod.ratio = ratio
            decimate_mod.use_collapse_triangulate = True

            if use_symmetry:
                decimate_mod.use_symmetry = True

            # Apply the modifier
            bpy.ops.object.modifier_apply(modifier=decimate_mod.name)

        final_poly_count = len(new_mesh.polygons)
        result[lod_name] = final_poly_count
        reduction_pct = ((original_poly_count - final_poly_count) / original_poly_count * 100) if original_poly_count > 0 else 0
        report_lines.append(f"      {lod_name.upper()}: {final_poly_count:,} polygons ({reduction_pct:.1f}% reduction)")

    # Restore previous state
    bpy.ops.object.mode_set(mode="OBJECT")
    lods.active_lod_level = previous_lod_level
    bpy.ops.object.mode_set(mode=previous_mode)

    return result


CMD = (CMD_ID, main)
