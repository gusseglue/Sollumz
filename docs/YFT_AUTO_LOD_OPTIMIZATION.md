# Automatic LOD Optimization for YFT Files

## Overview

The Automatic LOD Optimization feature allows you to automatically generate multiple Level of Detail (LOD) meshes for vehicle YFT files with intelligent polygon reduction. This feature is designed to optimize vehicle models for better performance in GTA V while maintaining visual quality.

## Features

- **Automatic LOD Generation**: Creates LOD_0 (High), LOD_1 (Medium), LOD_2 (Low), and LOD_3 (Very Low) automatically
- **Configurable Target Counts**: Set custom polygon count targets for each LOD level
- **Intelligent Decimation**: Uses Blender's decimate modifier with smart reduction
- **Preservation Options**: Maintains UV maps, vertex colors, and normals
- **Batch Processing**: Process multiple YFT files via CLI
- **Detailed Reports**: Get before/after polygon counts for each model

## Using the UI Operator

### Prerequisites

- A Fragment object must be loaded in Blender
- The Fragment must contain a Drawable with at least one Drawable Model
- At least one LOD level must have a source mesh

### Steps

1. Select any object within a Fragment hierarchy
2. Open the **Sollumz Tools** panel in the 3D Viewport
3. Navigate to **Fragments > Vehicle Tools**
4. Click **Automatic LOD & Optimization**
5. Configure the settings in the dialog:
   - **LOD Target Polygon Counts**: Set the target polygon count for each LOD level
     - LOD High (LOD_0): Default 200,000 polygons
     - LOD Medium (LOD_1): Default 100,000 polygons
     - LOD Low (LOD_2): Default 50,000 polygons
     - LOD Very Low (LOD_3): Default 10,000 polygons
   - **Decimation Options**:
     - Preserve UVs: Maintain UV mapping during reduction
     - Preserve Vertex Colors: Keep vertex color data
     - Use Symmetry: Apply symmetrical decimation for better results
     - Triangulate: Keep triangulated faces after decimation
6. Click **OK** to start the optimization

### Results

After processing, you'll see:
- A detailed report in the Blender console showing polygon counts for each model and LOD level
- New LOD meshes created for each Drawable Model
- A summary showing total polygon reduction

Example output:
```
=== YFT LOD Optimization Report ===
Fragment: sultan
Models processed: 5

  sultan_body:
    Original: 45,678 polygons
    HIGH: 45,678 polygons (0.0% reduction)
    MEDIUM: 25,430 polygons (44.3% reduction)
    LOW: 12,234 polygons (73.2% reduction)
    VERYLOW: 4,567 polygons (90.0% reduction)

=== Summary ===
Original total polygons: 152,340
LOD High total: 152,340
LOD Medium total: 85,123
LOD Low total: 42,567
LOD Very Low total: 15,234
```

## Using the CLI Command

The CLI command allows you to process multiple YFT files in batch mode without opening the Blender UI.

### Command Syntax

```bash
blender --background --command sz_yft_optimize [options] file1.yft.xml file2.yft.xml ...
```

### Options

- `files`: One or more YFT files to process (required)
- `-o, --output-dir PATH`: Output directory for optimized files (default: in-place)
- `--lod-high NUM`: Target polygon count for LOD High (default: 200000)
- `--lod-medium NUM`: Target polygon count for LOD Medium (default: 100000)
- `--lod-low NUM`: Target polygon count for LOD Low (default: 50000)
- `--lod-verylow NUM`: Target polygon count for LOD Very Low (default: 10000)
- `--preserve-uvs`: Preserve UV maps during decimation (default: True)
- `--preserve-vertex-colors`: Preserve vertex colors during decimation (default: True)
- `--use-symmetry`: Use symmetrical decimation (default: True)
- `--report PATH`: Output file for optimization report

### Example Usage

**Process a single file:**
```bash
blender --background --command sz_yft_optimize sultan.yft.xml
```

**Process multiple files with custom LOD targets:**
```bash
blender --background --command sz_yft_optimize \
  --lod-high 150000 \
  --lod-medium 75000 \
  --lod-low 35000 \
  --lod-verylow 8000 \
  sultan.yft.xml infernus.yft.xml zentorno.yft.xml
```

**Batch process with output directory and report:**
```bash
blender --background --command sz_yft_optimize \
  --output-dir ./optimized \
  --report ./optimization_report.txt \
  *.yft.xml
```

## Best Practices

### Target Polygon Counts

The default values are suitable for most vehicles:
- **LOD High (200k)**: Used when vehicle is close to the camera
- **LOD Medium (100k)**: Used at medium distance
- **LOD Low (50k)**: Used when vehicle is far away
- **LOD Very Low (10k)**: Used when vehicle is very far or many vehicles are visible

For specific vehicle types, consider:
- **Sports/Super cars**: Higher poly counts (maintain detail in curves)
- **Trucks/Vans**: Medium poly counts (simpler geometry)
- **Background vehicles**: Lower poly counts (not meant to be inspected closely)

### Quality Preservation

- **Always preserve UVs**: Essential for proper texture mapping
- **Preserve vertex colors**: Important for vehicle lights and paint
- **Use symmetry**: Recommended for vehicles (most are symmetrical)
- **Triangulate**: Generally recommended for game engines

### Workflow Integration

1. **Import your base YFT**: Start with a high-quality source model
2. **Run optimization**: Use the automatic LOD tool
3. **Review results**: Check LOD switching in the viewport
4. **Export**: Use standard Sollumz export (LODs are preserved)
5. **Test in-game**: Verify that LOD switching looks good

## Technical Details

### Decimation Algorithm

The tool uses Blender's built-in Decimate modifier with the "Collapse" method:
- Iteratively collapses edge pairs to reduce polygon count
- Maintains mesh topology and quality
- Preserves UV boundaries and vertex color data when enabled

### LOD Level Mapping

Sollumz LOD levels map to GTA V as follows:
- **LOD Level::VERYHIGH** → Not used in optimization (preserved if exists)
- **LOD Level::HIGH** → LOD_0 (High detail)
- **LOD Level::MEDIUM** → LOD_1 (Medium detail)
- **LOD Level::LOW** → LOD_2 (Low detail)
- **LOD Level::VERYLOW** → LOD_3 (Very low detail)

### Export Format

The optimized LODs are exported in standard YFT format:
- All LOD meshes are embedded in the same YFT file
- GTA V structure is maintained
- Compatible with existing YFT import/export workflow

## License

This feature is part of Sollumz and is licensed under the GNU General Public License v3.0.
