"""Paint System enum constants used by RNA properties and operators."""

import bpy

from ..custom_icons import get_icon
from .layer_types import build_layer_type_enum

BLEND_MODE_ENUM = []
for blend_mode in bpy.types.ShaderNodeMixRGB.bl_rna.properties['blend_type'].enum_items:
    BLEND_MODE_ENUM.append((blend_mode.identifier, blend_mode.name, blend_mode.description))
    if blend_mode.identifier in ["MIX", "COLOR_BURN", "ADD", "LINEAR_LIGHT", "DIVIDE"]:
        if blend_mode.identifier == "MIX":
            BLEND_MODE_ENUM.append(("PASSTHROUGH", "Pass Through", "Pass Through"))
        BLEND_MODE_ENUM.append(None)

MASK_BLEND_MODE_ENUM = [
    ('SUBTRACT', "Subtract", "Subtract"),
    ('ADD', "Add", "Add"),
    ('MULTIPLY', "Multiply", "Multiply"),
]

MASK_TYPE_ENUM = [
    ('VALUE', "Value", "Value mask"),
    ('IMAGE', "Image", "Image mask"),
    ('ATTRIBUTE', "Attribute", "Attribute mask"),
    ('TEXTURE', "Texture", "Texture mask"),
]

MASK_COORDINATE_TYPE_ENUM = [
    ('AUTO', "Auto UV", "Automatically create a new UV Map"),
    ('UV', "UV", "Open an existing UV Map"),
    ('OBJECT', "Object", "Use a object output of Texture Coordinate node"),
    ('POSITION', "Position", "Use a position output of Geometry node"),
    ('GENERATED', "Generated", "Use a generated output of Texture Coordinate node"),
]

TEMPLATE_ENUM = [
    ('BASIC', "Blank Canvas", "Blank canvas painting setup", "IMAGE", 0),
    ('PAINT_OVER', "Paint Over", "Paint over the existing material", get_icon('paintbrush'), 1),
    ('PBR', "PBR", "PBR painting setup", "MATERIAL", 2),
    ('NORMAL', "Normals Painting", "Start off with a normal painting setup", "NORMALS_VERTEX_FACE", 3),
    ('NONE', "None", "Just add node group to material", "NONE", 4),
]

LAYER_TYPE_ENUM = build_layer_type_enum()

CHANNEL_TYPE_ENUM = [
    ('COLOR', "Color", "Color channel", get_icon('color_socket'), 1),
    ('VECTOR', "Vector", "Vector channel", get_icon('vector_socket'), 2),
    ('FLOAT', "Value", "Value channel", get_icon('float_socket'), 3),
]

GRADIENT_TYPE_ENUM = [
    ('GRADIENT_MAP', "Gradient Map", "Gradient map"),
    ('LINEAR', "Linear Gradient", "Linear gradient"),
    ('RADIAL', "Radial Gradient", "Radial gradient"),
    ('DISTANCE', "Distance Gradient", "Distance gradient"),
    ('FAKE_LIGHT', "Fake Light", "Fake light"),
]

ADJUSTMENT_TYPE_ENUM = [
    ('BRIGHTCONTRAST', "Brightness and Contrast", ""),
    ('GAMMA', "Gamma", ""),
    ('HUE_SAT', "Hue Saturation Value", ""),
    ('INVERT', "Invert", ""),
    ('CURVE_RGB', "RGB Curves", ""),
    ('RGBTOBW', "RGB to BW", ""),
    ('MAP_RANGE', "Map Range", ""),
    # ('ShaderNodeAmbientOcclusion', "Ambient Occlusion", ""),
]

TEXTURE_TYPE_ENUM = [
    ('TEX_BRICK', "Brick Texture", ""),
    ('TEX_CHECKER', "Checker Texture", ""),
    # ('ShaderNodeTexGabor', "Gabor Texture", ""),
    ('TEX_GRADIENT', "Gradient Texture", ""),
    ('TEX_MAGIC', "Magic Texture", ""),
    ('TEX_NOISE', "Noise Texture", ""),
    ('TEX_VORONOI', "Voronoi Texture", ""),
    ('TEX_WAVE', "Wave Texture", ""),
    ('TEX_WHITE_NOISE', "White Noise Texture", ""),
]

COORDINATE_TYPE_ENUM = [
    ('AUTO', "Auto UV", "Automatically create a new UV Map"),
    ('UV', "UV", "Open an existing UV Map"),
    ('OBJECT', "Object", "Use a object output of Texture Coordinate node"),
    ('CAMERA', "Camera", "Use a camera output of Texture Coordinate node"),
    ('WINDOW', "Window", "Use a window output of Texture Coordinate node"),
    ('REFLECTION', "Reflection", "Use a reflection output of Texture Coordinate node"),
    ('POSITION', "Position", "Use a position output of Geometry node"),
    ('GENERATED', "Generated", "Use a generated output of Texture Coordinate node"),
    ('DECAL', "Decal", "Use a decal output of Geometry node"),
    ('PROJECT', "Projection", "Define a projection coordinate"),
    ('PARALLAX', 'Parallax', 'Use a parallax coordinate'),
]

ATTRIBUTE_TYPE_ENUM = [
    ('GEOMETRY', "Geometry", "Geometry"),
    ('OBJECT', "Object", "Object"),
    ('INSTANCER', "Instancer", "Instancer"),
    ('VIEW_LAYER', "View Layer", "View Layer")
]

GEOMETRY_TYPE_ENUM = [
    ('WORLD_NORMAL', "World Space Normal", "World Space Normal"),
    ('WORLD_TRUE_NORMAL', "World Space True Normal", "World Space True Normal"),
    ('POSITION', "World Space Position", "World Space Position"),
    ('OBJECT_NORMAL', "Object Space Normal", "Object Space Normal"),
    ('OBJECT_POSITION', "Object Space Position", "Object Space Position"),
    ('BACKFACING', "Backfacing", "Backfacing"),
    ('VECTOR_TRANSFORM', "Vector Transform", "Vector Transform"),
    ('AMBIENT_OCCLUSION', "Ambient Occlusion", "Ambient Occlusion"),
]

ACTION_TYPE_ENUM = [
    ('ENABLE', "Enable Layer", "Enable the layer when reached"),
    ('DISABLE', "Disable Layer", "Disable the layer when reached"),
]

ACTION_BIND_ENUM = [
    ('FRAME', "Frame", "Enable/disable the layer on a frame", "KEYTYPE_KEYFRAME_VEC", 0),
    ('MARKER', "Marker", "Enable/disable the layer on a marker", "MARKER_HLT", 1),
]

COLOR_SPACE_ENUM = [
    ('COLOR', "Color", "Color"),
    ('NONCOLOR', "Non-Color", "Non-Color"),
]

FILTER_TYPE_ENUM = [
    ('BLUR', "Blur", "Blur"),
    ('EDGE_ENHANCE', "Edge Enhance", "Edge Enhance"),
    ('SHARPEN', "Sharpen", "Sharpen"),
]

PARALLAX_TYPE_ENUM = [
    ('UV', "UV", "UV"),
    ('Object', "Object", "Object"),
]

EDIT_EXTERNAL_MODE_ENUM = [
    ('IMAGE_EDIT', "Image Edit", "Edit Image in external editor", "IMAGE", 0),
    ('VIEW_CAPTURE', "View Capture", "Capture view and edit in external editor", "CAMERA_DATA", 1),
]


CHANNEL_TEMPLATE_ENUM = [
    ("COLOR", "Color", "Color", get_icon('color_socket'), 0),
    ("METALLIC", "Metallic", "Metallic", get_icon('float_socket'), 1),
    ("ROUGHNESS", "Roughness", "Roughness", get_icon('float_socket'), 2),
    ("NORMAL", "Normal", "Normal", get_icon('vector_socket'), 3),
]
