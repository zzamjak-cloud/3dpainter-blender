import bpy
from bpy.utils import register_submodule_factory

submodules = [
    # "graph",
    "layers_operators",
    "channel_operators",
    "group_operators",
    "utils_operators",
    "image_operators",
    "quick_edit",
    "versioning_operators",
    "bake_operators",
    "shader_editor",
    # 3DPainter 포크 추가 모듈
    "line_operators",
    "eyedropper_operators",
    "precision_operators",
    "view2d_operators",
    "lasso_operators",
    "psd_operators",
    "paint3d_operators",
    "projection_operators",
    "merge_operators",
    "art_brush_operators",
    "painting_ux_operators",
    "parts_operators",
]

register, unregister = register_submodule_factory(__name__, submodules)