# SPDX-License-Identifier: GPL-3.0-or-later
# 3DPainter 포크 추가 기능: Projection Tex — 2D 이미지를 현재 뷰에서 모델에 투사
#
# 흐름: 이미지 등록(Import) → 뷰포트 오버레이로 위치/스케일 조정(모달)
# → Enter 시 리전 크기 캔버스에 합성 → 신규 레이어 생성 →
# 네이티브 paint.project_image로 투사 (오클루전·심 블리드 자동 처리).

import os
import sys

import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

import bpy
from bpy.props import (
    CollectionProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import Operator, PropertyGroup

from .common import ModalDrawMixin, PSContextMixin
from .psd_operators import channel_coord_settings
from ..paintsystem.image import read_rgba, write_rgba
from ..utils.imaging import bilinear_resize
from ..utils.registration import collect_classes


class PSProjectionTexItem(PropertyGroup):
    name: StringProperty(name="Name")
    filepath: StringProperty(subtype='FILE_PATH')
    image_name: StringProperty()
    mtime: FloatProperty(default=0.0)

    def get_image(self):
        return bpy.data.images.get(self.image_name)

    def ensure_image(self):
        """이미지가 없으면 파일에서 다시 로드한다 (poll/draw에서는 호출 금지 —
        ID 쓰기가 막혀 있으므로 invoke/execute에서만)."""
        img = bpy.data.images.get(self.image_name)
        if img is None and self.filepath and os.path.isfile(self.filepath):
            try:
                img = bpy.data.images.load(self.filepath, check_existing=True)
                self.image_name = img.name
            except RuntimeError:
                return None
        return img


def _active_item(context):
    scene = context.scene
    items = scene.ps_projection_textures
    idx = scene.ps_projection_active_index
    if 0 <= idx < len(items):
        return items[idx]
    return None


# ---- 썸네일 프리뷰 ----

_pcoll = None
_enum_items_cache = []  # bpy enum 콜백 문자열의 GC 방지용 유지 참조


def _previews():
    global _pcoll
    if _pcoll is None:
        import bpy.utils.previews
        _pcoll = bpy.utils.previews.new()
    return _pcoll


def _clear_previews():
    global _pcoll
    if _pcoll is not None:
        import bpy.utils.previews
        bpy.utils.previews.remove(_pcoll)
        _pcoll = None


def _thumb_icon_id(item) -> int:
    pcoll = _previews()
    key = item.filepath
    if key not in pcoll:
        try:
            pcoll.load(key, item.filepath, 'IMAGE')
        except (KeyError, RuntimeError):
            return 0
    return pcoll[key].icon_id


def _projection_enum_items(self, context):
    global _enum_items_cache
    if context is None:
        return [('0', "", "", 0, 0)]
    items = []
    for i, item in enumerate(context.scene.ps_projection_textures):
        # 마우스 호버 시 이름 표시 (툴팁 = 파일명)
        items.append((str(i), item.name, item.filepath, _thumb_icon_id(item), i))
    if not items:
        items = [('0', "(empty)", "등록된 이미지 없음", 0, 0)]
    _enum_items_cache = items
    return items


def _enum_get(self):
    n = len(self.ps_projection_textures)
    if n == 0:
        return 0
    return min(max(self.ps_projection_active_index, 0), n - 1)


def _enum_set(self, value):
    self.ps_projection_active_index = value


class PAINTSYSTEM_OT_ProjectionImport(Operator):
    """이미지 파일(JPG/PNG/PSD)을 투사 목록에 등록한다 (다중 선택 가능)"""
    bl_idname = "paint_system.projection_import"
    bl_label = "Import Projection Image"
    bl_options = {'REGISTER'}

    filepath: StringProperty(subtype='FILE_PATH')
    directory: StringProperty(subtype='DIR_PATH')
    files: CollectionProperty(type=bpy.types.OperatorFileListElement)
    filter_glob: StringProperty(
        default='*.jpg;*.jpeg;*.png;*.psd', options={'HIDDEN'})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        scene = context.scene
        paths = [os.path.join(self.directory, f.name) for f in self.files if f.name]
        if not paths and self.filepath:
            paths = [self.filepath]
        count = 0
        for path in paths:
            if not os.path.isfile(path):
                continue
            try:
                img = bpy.data.images.load(path, check_existing=True)
            except RuntimeError as e:
                self.report({'WARNING'}, f"불러오기 실패: {os.path.basename(path)} ({e})")
                continue
            item = scene.ps_projection_textures.add()
            item.name = os.path.basename(path)
            item.filepath = path
            item.image_name = img.name
            item.mtime = os.path.getmtime(path)
            count += 1
        if count:
            scene.ps_projection_active_index = len(scene.ps_projection_textures) - 1
        self.report({'INFO'}, f"투사 이미지 {count}개 등록")
        return {'FINISHED'}


class PAINTSYSTEM_OT_ProjectionSelect(Operator):
    """투사 이미지를 선택한다 (그리드 썸네일 클릭)"""
    bl_idname = "paint_system.projection_select"
    bl_label = "Select Projection Image"
    bl_options = {'INTERNAL'}

    index: IntProperty()

    @classmethod
    def description(cls, context, properties):
        # 호버 툴팁 = 이미지 이름
        items = context.scene.ps_projection_textures
        if 0 <= properties.index < len(items):
            return items[properties.index].name
        return ""

    def execute(self, context):
        scene = context.scene
        if 0 <= self.index < len(scene.ps_projection_textures):
            scene.ps_projection_active_index = self.index
        return {'FINISHED'}


class PAINTSYSTEM_OT_ProjectionRemove(Operator):
    """선택한 투사 이미지를 목록에서 제거한다"""
    bl_idname = "paint_system.projection_remove"
    bl_label = "Remove Projection Image"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _active_item(context) is not None

    def execute(self, context):
        scene = context.scene
        idx = scene.ps_projection_active_index
        item = scene.ps_projection_textures[idx]
        img = item.get_image()
        if img is not None and img.users <= 1:
            bpy.data.images.remove(img)
        scene.ps_projection_textures.remove(idx)
        scene.ps_projection_active_index = min(
            idx, len(scene.ps_projection_textures) - 1)
        return {'FINISHED'}


class PAINTSYSTEM_OT_ProjectionPlace(ModalDrawMixin, PSContextMixin, Operator):
    """투사 이미지를 뷰포트에 배치한다 — 드래그: 이동, 휠: 크기,
    Enter: 신규 레이어로 투사 적용, ESC/우클릭: 취소"""
    bl_idname = "paint_system.projection_place"
    bl_label = "Place Projection"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        item = _active_item(context)
        if item is None:
            return False
        if item.get_image() is None and not os.path.isfile(item.filepath):
            return False
        obj = context.active_object
        if obj is None or obj.type != 'MESH':
            return False
        try:
            return PSContextMixin.parse_context(context).active_channel is not None
        except Exception:
            return False

    def invoke(self, context, event):
        if context.area is None or context.area.type != 'VIEW_3D':
            self.report({'WARNING'}, "3D 뷰에서 실행하세요")
            return {'CANCELLED'}
        # 페인트 모드가 아니면 자동 진입 (Apply의 project_image가 요구)
        if context.mode != 'PAINT_TEXTURE':
            try:
                bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
            except RuntimeError:
                self.report({'WARNING'}, "텍스처 페인트 모드로 전환할 수 없습니다")
                return {'CANCELLED'}
        region = context.region
        if region is None:
            # 패널 버튼에서 호출되면 region이 UI 리전이므로 WINDOW 리전을 찾는다
            region = next(
                (r for r in context.area.regions if r.type == 'WINDOW'), None)
            if region is None:
                return {'CANCELLED'}
        self._region_ptr = region.as_pointer()
        self._item = _active_item(context)
        img = self._item.ensure_image()
        if img is None:
            self.report({'ERROR'}, "이미지를 불러올 수 없습니다 (파일 확인)")
            return {'CANCELLED'}
        sw, sh = int(img.size[0]), int(img.size[1])
        if sw == 0 or sh == 0:
            self.report({'ERROR'}, "이미지를 읽을 수 없습니다")
            return {'CANCELLED'}
        self._img_size = (sw, sh)
        self._center = [region.width * 0.5, region.height * 0.5]
        self._scale = min(region.width / sw, region.height / sh) * 0.5
        self._dragging = False
        self._drag_offset = (0.0, 0.0)
        self._add_view3d_draw_handler(self._draw, ())
        context.window_manager.modal_handler_add(self)
        context.area.header_text_set(
            "투사 배치 — 드래그: 이동 · 휠: 크기 · Enter: 적용 · ESC: 취소")
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        context.area.tag_redraw()
        if event.type == 'LEFTMOUSE':
            if event.value == 'PRESS':
                self._dragging = True
                self._drag_offset = (
                    self._center[0] - event.mouse_region_x,
                    self._center[1] - event.mouse_region_y,
                )
            elif event.value == 'RELEASE':
                self._dragging = False
            return {'RUNNING_MODAL'}
        if event.type == 'MOUSEMOVE' and self._dragging:
            self._center[0] = event.mouse_region_x + self._drag_offset[0]
            self._center[1] = event.mouse_region_y + self._drag_offset[1]
            return {'RUNNING_MODAL'}
        if event.type in {'WHEELUPMOUSE', 'EQUAL', 'NUMPAD_PLUS'}:
            self._scale *= 1.05
            return {'RUNNING_MODAL'}
        if event.type in {'WHEELDOWNMOUSE', 'MINUS', 'NUMPAD_MINUS'}:
            self._scale = max(self._scale / 1.05, 0.01)
            return {'RUNNING_MODAL'}
        if event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
            self._finish(context)
            return self._apply(context)
        if event.type in {'RIGHTMOUSE', 'ESC'}:
            self._finish(context)
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}  # 그 외 이벤트 소비 → 뷰포트 고정

    def _finish_modal_draw(self, context):
        self._remove_view3d_draw_handler()
        if context.area:
            context.area.header_text_set(None)
            context.area.tag_redraw()

    def _finish(self, context):
        self._finish_modal_draw(context)

    def _rect(self):
        sw, sh = self._img_size
        dw, dh = sw * self._scale, sh * self._scale
        x0 = self._center[0] - dw * 0.5
        y0 = self._center[1] - dh * 0.5
        return x0, y0, dw, dh

    def _draw(self):
        region = bpy.context.region
        if region is None or region.as_pointer() != self._region_ptr:
            return
        img = self._item.get_image()
        if img is None:
            return
        x0, y0, dw, dh = self._rect()
        try:
            tex = gpu.texture.from_image(img)
            shader = gpu.shader.from_builtin('IMAGE')
            batch = batch_for_shader(
                shader, 'TRI_FAN',
                {
                    "pos": ((x0, y0), (x0 + dw, y0),
                            (x0 + dw, y0 + dh), (x0, y0 + dh)),
                    "texCoord": ((0, 0), (1, 0), (1, 1), (0, 1)),
                },
            )
            gpu.state.blend_set('ALPHA')
            shader.uniform_sampler("image", tex)
            batch.draw(shader)
            gpu.state.blend_set('NONE')
        except Exception:
            pass

    def _apply(self, context):
        region = context.region
        img = self._item.get_image()
        rw, rh = region.width, region.height

        # 1. 리전 크기 캔버스에 오버레이와 동일한 배치로 합성
        src = read_rgba(img)
        x0, y0, dw, dh = self._rect()
        dw_i, dh_i = max(int(round(dw)), 1), max(int(round(dh)), 1)
        resized = bilinear_resize(src, dh_i, dw_i)

        canvas = np.zeros((rh, rw, 4), dtype=np.float32)
        dx0, dy0 = int(round(x0)), int(round(y0))
        cx0, cy0 = max(dx0, 0), max(dy0, 0)
        cx1, cy1 = min(dx0 + dw_i, rw), min(dy0 + dh_i, rh)
        if cx1 <= cx0 or cy1 <= cy0:
            self.report({'WARNING'}, "이미지가 뷰포트 밖에 있습니다")
            return {'CANCELLED'}
        canvas[cy0:cy1, cx0:cx1] = resized[
            cy0 - dy0:cy1 - dy0, cx0 - dx0:cx1 - dx0]

        # project_image는 소스 알파를 보존하지 않아 투명 영역이 검게 칠해진다.
        # → RGB용(알파 1 강제)과 알파용(알파를 RGB로 복제) 두 장으로 나눠
        #   같은 카메라로 각각 투사한 뒤 결합한다.
        alpha_ch = canvas[:, :, 3].copy()
        rgb_np = canvas.copy()
        rgb_np[:, :, 3] = 1.0
        alpha_np = np.empty_like(canvas)
        alpha_np[:, :, 0] = alpha_ch
        alpha_np[:, :, 1] = alpha_ch
        alpha_np[:, :, 2] = alpha_ch
        alpha_np[:, :, 3] = 1.0

        temp = bpy.data.images.new(
            "PS Projection Temp", width=rw, height=rh, alpha=True)
        write_rgba(temp, rgb_np, tag=False)
        temp_alpha = bpy.data.images.new(
            "PS Projection Temp Alpha", width=rw, height=rh, alpha=True)
        write_rgba(temp_alpha, alpha_np, tag=False)

        try:
            # 2. 신규 레이어 생성 (현재 캔버스 해상도)
            ps_ctx = self.parse_context(context)
            channel = ps_ctx.active_channel
            ip = context.tool_settings.image_paint
            base = ip.canvas
            lw = int(base.size[0]) if base is not None and base.size[0] else 2048
            lh = int(base.size[1]) if base is not None and base.size[1] else 2048
            layer_img = bpy.data.images.new(
                f"Projection {self._item.name}", width=lw, height=lh, alpha=True)
            coord_type, uv_map_name = channel_coord_settings(context, channel)
            channel.create_layer(
                context, layer_name=f"Projection {self._item.name}",
                layer_type='IMAGE', image=layer_img, insert_at='TOP',
                coord_type=coord_type, uv_map_name=uv_map_name)
            # 3. 네이티브 투사 — project_image는 씬 카메라 기준이므로
            # 현재 뷰포트와 일치하는 임시 카메라를 만들어 RGB/알파를 각각 투사
            scratch = bpy.data.images.new(
                "PS Projection Scratch", width=lw, height=lh, alpha=True)
            try:
                self._project_from_view(
                    context, [(layer_img, temp), (scratch, temp_alpha)], rw, rh)

                # 4. 알파 결합: 투사된 알파(스크래치의 R)를 레이어 알파로
                lb = read_rgba(layer_img)
                sb = read_rgba(scratch)
                lb[:, :, 3] = sb[:, :, 0]
                write_rgba(layer_img, lb)
            finally:
                bpy.data.images.remove(scratch)

            ip.canvas = layer_img
            from .view2d_operators import ensure_composite_shading
            ensure_composite_shading(context)
        finally:
            bpy.data.images.remove(temp)
            bpy.data.images.remove(temp_alpha)

        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
        self.report({'INFO'}, f"투사 완료 → 레이어 'Projection {self._item.name}'")
        return {'FINISHED'}

    @staticmethod
    def _project_from_view(context, jobs, rw: int, rh: int) -> None:
        """현재 뷰포트와 동일한 임시 카메라로 (캔버스, 소스) 쌍을 차례로 투사한다."""
        import math

        scene = context.scene
        space = context.space_data
        rv3d = context.region.data
        wm = rv3d.window_matrix

        cam_data = bpy.data.cameras.new("PS Proj Cam")
        cam_obj = bpy.data.objects.new("PS Proj Cam", cam_data)
        context.collection.objects.link(cam_obj)
        cam_obj.matrix_world = rv3d.view_matrix.inverted()
        cam_data.sensor_fit = 'HORIZONTAL'
        if rv3d.view_perspective == 'ORTHO':
            cam_data.type = 'ORTHO'
            cam_data.ortho_scale = 2.0 / wm[0][0]
        else:
            cam_data.type = 'PERSP'
            cam_data.angle_x = 2.0 * math.atan(1.0 / wm[0][0])
        if space is not None:
            cam_data.clip_start = space.clip_start
            cam_data.clip_end = space.clip_end

        render = scene.render
        prev = (scene.camera, render.resolution_x, render.resolution_y,
                render.resolution_percentage)
        scene.camera = cam_obj
        render.resolution_x = rw
        render.resolution_y = rh
        render.resolution_percentage = 100
        ip = context.tool_settings.image_paint
        try:
            for canvas_img, src_img in jobs:
                ip.canvas = canvas_img
                bpy.ops.paint.project_image(image=src_img.name)
        finally:
            scene.camera = prev[0]
            render.resolution_x = prev[1]
            render.resolution_y = prev[2]
            render.resolution_percentage = prev[3]
            bpy.data.objects.remove(cam_obj)
            bpy.data.cameras.remove(cam_data)


def _autoreload_timer():
    """등록된 투사 이미지의 원본 파일 변경을 감시해 자동 리로드한다."""
    scene = bpy.context.scene
    if scene is None:
        return 2.0
    items = getattr(scene, 'ps_projection_textures', None)
    if not items:
        return 2.0
    for item in items:
        try:
            if not item.filepath or not os.path.isfile(item.filepath):
                continue
            mtime = os.path.getmtime(item.filepath)
            if mtime > item.mtime + 1e-4:
                item.mtime = mtime
                img = item.get_image()
                if img is not None:
                    img.reload()
                    if hasattr(img, 'update_tag'):
                        img.update_tag()
                _clear_previews()  # 썸네일도 다음 표시 때 재생성
        except Exception:
            continue
    return 2.0


classes = collect_classes(sys.modules[__name__])


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ps_projection_textures = CollectionProperty(
        type=PSProjectionTexItem)
    bpy.types.Scene.ps_projection_active_index = IntProperty(default=0)
    bpy.types.Scene.ps_projection_enum = bpy.props.EnumProperty(
        items=_projection_enum_items,
        get=_enum_get,
        set=_enum_set,
        name="Projection Image",
    )
    bpy.types.Scene.ps_projection_thumb_scale = FloatProperty(
        name="Thumbnail Size", default=6.0, min=2.0, max=12.0)
    bpy.app.timers.register(_autoreload_timer, first_interval=2.0, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_autoreload_timer):
        bpy.app.timers.unregister(_autoreload_timer)
    _clear_previews()
    del bpy.types.Scene.ps_projection_textures
    del bpy.types.Scene.ps_projection_active_index
    del bpy.types.Scene.ps_projection_enum
    del bpy.types.Scene.ps_projection_thumb_scale
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
