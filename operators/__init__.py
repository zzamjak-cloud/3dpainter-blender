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
]

register, unregister = register_submodule_factory(__name__, submodules)