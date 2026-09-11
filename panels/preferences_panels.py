import sys

import bpy
from bpy.types import AddonPreferences
from bpy.props import BoolProperty, FloatProperty, EnumProperty, IntProperty
from bpy.utils import register_classes_factory

from .common import find_keymap
from ..preferences import addon_package
from ..utils.registration import collect_classes

class PaintSystemPreferences(AddonPreferences):
    """Demo bare-bones preferences"""
    bl_idname = addon_package()

    show_tooltips: BoolProperty(
        name="Show Tooltips",
        description="Show tooltips in the UI",
        default=True
    )
    show_hex_color: BoolProperty(
        name="Show Hex Color",
        description="Show hex color in the color picker settings",
        default=False
    )
    show_more_color_picker_settings: BoolProperty(
        name="Show More Color Picker Settings",
        description="Show more color picker settings",
        default=False
    )
    
    show_opacity_in_layer_list: BoolProperty(
        name="Show Opacity in Layer List",
        description="Show the opacity in the layer list",
        default=True
    )

    use_compact_design: BoolProperty(
        name="Use Compact Design",
        description="Use a more compact design for the UI",
        default=False
    )
    
    color_picker_scale: FloatProperty(
        name="Color Picker Scale",
        description="Scale the color picker",
        default=1.0,
        min=0.5,
        max=3.0
    )
    
    preferred_coord_type: EnumProperty(
        name="Preferred Coordinate Type",
        description="Preferred coordinate type",
        items=(
            ('AUTO', 'Auto UV', ''),
            ('UV', 'UV', ''),
            ('UNDETECTED', 'Undetected', ''),
        ),
        default='UNDETECTED',
    )

    color_picker_scale_rmb: FloatProperty(
        name="RMB Color Wheel Scale",
        description="Scale the color wheel in the Texture Paint right-click popover",
        default=1.2,
        min=0.5,
        max=3.0
    )
    
    # Tips
    hide_norm_paint_tips: BoolProperty(
        name="Hide Normal Painting Tips",
        description="Hide the normal painting tips",
        default=False
    )
    hide_color_attr_tips: BoolProperty(
        name="Hide Color Attribute Tips",
        description="Hide the color attribute tips",
        default=False
    )

    use_legacy_ui: BoolProperty(
        name="Use Legacy UI",
        description="Use the legacy UI",
        default=False
    )
    
    use_panel_quick_access: BoolProperty(
        name="Use Panel Quick Access",
        description="Use the panel quick access",
        default=False
    )
    
    developer_mode: BoolProperty(
        name="Developer Mode",
        description="Enable developer mode for verbose logging",
        default=False
    )

    # RMB popover options
    show_hsv_sliders_rmb: BoolProperty(
        name="Show Hue/Saturation/Value sliders (RMB)",
        description="Show HSV sliders under the color wheel in the Texture Paint right-click popover",
        default=False
    )
    show_active_palette_rmb: BoolProperty(
        name="Show Active Palette (RMB)",
        description="Show the active palette swatches in the Texture Paint right-click popover",
        default=True
    )
    show_brush_settings_rmb: BoolProperty(
        name="Show Brush Controls (RMB)",
        description="Show brush radius/strength controls in the Texture Paint right-click popover",
        default=True
    )

    # --- 페인팅 정밀도 (포토샵 감각 맞추기) ---
    texture_interpolation: EnumProperty(
        name="Texture Filtering",
        description="이미지 레이어를 3D 뷰에 표시할 때 쓰는 텍셀 보간 방식",
        items=(
            ('Linear', "Linear (Smooth)",
             "텍셀 사이를 보간해 포토샵처럼 부드러운 가장자리를 얻는다"),
            ('Closest', "Closest (Pixel)",
             "보간 없이 텍셀을 그대로 표시한다 — 픽셀 아트용"),
        ),
        default='Linear',
    )
    use_brush_precision: BoolProperty(
        name="Photoshop-style Brush Dynamics",
        description=(
            "브러시를 처음 쓸 때 필압(크기·불투명도)과 입력 샘플을 포토샵에 가깝게 "
            "1회 초기화한다. 이후 사용자가 바꾼 값은 건드리지 않는다"),
        default=True
    )
    brush_input_samples: IntProperty(
        name="Input Samples",
        description=(
            "태블릿 입력을 몇 개씩 평균낼지 — 값이 클수록 획이 매끄럽지만 "
            "커서 반응이 조금 늦어진다"),
        default=4, min=1, max=32
    )
    brush_spacing: IntProperty(
        name="Brush Spacing",
        description="스탬프 간격(지름 대비 %). 낮을수록 획이 촘촘해 각지지 않는다",
        default=5, min=1, max=100
    )

    def draw_shortcut(self, layout, kmi, text):
        row = layout.row(align=True)
        row.prop(kmi, "active", text="", emboss=False)
        row.label(text=text)
        row.prop(kmi, "map_type", text="")
        map_type = kmi.map_type
        if map_type == 'KEYBOARD':
            row.prop(kmi, "type", text="", full_event=True)
        elif map_type == 'MOUSE':
            row.prop(kmi, "type", text="", full_event=True)
        elif map_type == 'NDOF':
            row.prop(kmi, "type", text="", full_event=True)
        elif map_type == 'TWEAK':
            subrow = row.row()
            subrow.prop(kmi, "type", text="")
            subrow.prop(kmi, "value", text="")
        elif map_type == 'TIMER':
            row.prop(kmi, "type", text="")
        else:
            row.label()

        if (not kmi.is_user_defined) and kmi.is_user_modified:
            row.operator("preferences.keyitem_restore", text="", icon='BACK').item_id = kmi.id

    def draw(self, context):
        layout = self.layout

        layout.prop(self, "show_tooltips", text="Show Tooltips")
        layout.prop(self, "use_compact_design", text="Use Compact Design")
        layout.prop(self, "show_opacity_in_layer_list", text="Show Opacity in Layer List")
        layout.prop(self, "use_legacy_ui", text="Use Legacy UI")
        layout.prop(self, "use_panel_quick_access", text="Use Panel Quick Access")
        # layout.prop(self, "name_layers_group",
        #             text="Name Layers According to Group Name")

        dev_box = layout.box()
        dev_box.label(text="Advanced", icon='PREFERENCES')
        dev_box.prop(self, "developer_mode", text="Developer Mode")

        # --- Texture Paint Right Click Menu ---
        rmb_box = layout.box()
        rmb_box.label(text="Texture Paint Right Click Menu", icon='MOUSE_RMB')
        rmb_box.prop(self, "color_picker_scale_rmb", text="Color Wheel Scale")
        rmb_box.prop(self, "show_hsv_sliders_rmb", text="Show HSV sliders in RMB popover")
        # rmb_box.prop(self, "show_active_palette_rmb", text="Show Active Palette in RMB popover")
        rmb_box.prop(self, "show_brush_settings_rmb", text="Show Brush Controls in RMB popover")

        # --- 페인팅 정밀도 ---
        prec_box = layout.box()
        prec_box.label(text="Painting Precision", icon='BRUSH_DATA')
        prec_box.prop(self, "texture_interpolation")
        prec_box.operator("paint_system.apply_texture_interpolation",
                          text="Apply Filtering to All Image Layers", icon='FILE_REFRESH')
        prec_box.separator()
        prec_box.prop(self, "use_brush_precision")
        col = prec_box.column(align=True)
        col.enabled = self.use_brush_precision
        col.prop(self, "brush_input_samples")
        col.prop(self, "brush_spacing")
        prec_box.operator("paint_system.setup_brush_precision",
                          text="Apply to Current Brush", icon='BRUSH_DATA')

        box = layout.box()
        box.label(text="Paint System Shortcuts:")
        kmi = find_keymap('paint_system.color_sample')
        if kmi:
            self.draw_shortcut(box, kmi, "Color Sampler Shortcut")
        kmi = find_keymap('paint_system.toggle_brush_erase_alpha')
        if kmi:
            self.draw_shortcut(box, kmi, "Toggle Eraser")
        kmi = find_keymap('paint_system.toggle_pressure_strength')
        if kmi:
            self.draw_shortcut(box, kmi, "Toggle Strength Pressure")
        kmi = find_keymap('paint_system.toggle_pressure_size')
        if kmi:
            self.draw_shortcut(box, kmi, "Toggle Size Pressure")

classes = collect_classes(sys.modules[__name__])

register, unregister = register_classes_factory(classes)