# SPDX-License-Identifier: GPL-3.0-or-later
# 3DPainter 포크 추가 기능: 웹앱(.paint3d) 프로젝트 임포터 — 기존 자산 이전용
#
# .paint3d = zip { project.json, layers/<id>.png, model/<원본 OBJ|FBX> }
# project.json.layers는 아래→위 순서, blendMode는 Canvas2D 합성 문자열.

import json
import os
import tempfile
import zipfile

import bpy
from bpy.props import StringProperty
from bpy.types import Operator

from .common import PSContextMixin
from .psd_operators import _set_layer_opacity, channel_coord_settings

# Canvas2D globalCompositeOperation → Paint System 블렌드 모드
_WEB_TO_PS_BLEND = {
    'source-over': 'MIX', 'multiply': 'MULTIPLY', 'screen': 'SCREEN',
    'overlay': 'OVERLAY', 'darken': 'DARKEN', 'lighten': 'LIGHTEN',
    'color-dodge': 'DODGE', 'color-burn': 'BURN', 'hard-light': 'OVERLAY',
    'soft-light': 'SOFT_LIGHT', 'difference': 'DIFFERENCE',
    'exclusion': 'EXCLUSION', 'hue': 'HUE', 'saturation': 'SATURATION',
    'color': 'COLOR', 'luminosity': 'VALUE', 'lighter': 'ADD',
}


def _import_model(tmpdir: str, rel_path: str, data: bytes):
    """동봉된 모델 파일을 임포트하고 새 오브젝트를 활성화한다."""
    path = os.path.join(tmpdir, os.path.basename(rel_path))
    with open(path, 'wb') as f:
        f.write(data)
    before = set(bpy.data.objects)
    ext = os.path.splitext(path)[1].lower()
    if ext == '.obj':
        bpy.ops.wm.obj_import(filepath=path)
    elif ext == '.fbx':
        bpy.ops.import_scene.fbx(filepath=path)
    else:
        raise RuntimeError(f"지원하지 않는 모델 형식: {ext}")
    new_objs = [o for o in bpy.data.objects if o not in before and o.type == 'MESH']
    if not new_objs:
        raise RuntimeError("모델 임포트 결과 메시가 없습니다")
    obj = new_objs[0]
    bpy.context.view_layer.objects.active = obj
    for o in bpy.context.view_layer.objects:
        o.select_set(o == obj)
    return obj


class PAINTSYSTEM_OT_ImportPaint3D(PSContextMixin, Operator):
    """웹앱 .paint3d 프로젝트를 가져온다 (모델 + 레이어 스택)"""
    bl_idname = "paint_system.import_paint3d"
    bl_label = "Import .paint3d"
    bl_options = {'REGISTER', 'UNDO'}

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default='*.paint3d', options={'HIDDEN'})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        path = bpy.path.abspath(self.filepath)
        if not os.path.isfile(path):
            self.report({'ERROR'}, "파일을 찾을 수 없습니다")
            return {'CANCELLED'}

        try:
            zf = zipfile.ZipFile(path)
            meta = json.loads(zf.read('project.json'))
        except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as e:
            self.report({'ERROR'}, f"올바른 .paint3d 파일이 아닙니다: {e}")
            return {'CANCELLED'}

        with tempfile.TemporaryDirectory() as tmpdir:
            # 1. 모델: 동봉돼 있으면 임포트, 없으면 활성 메시 사용 (구버전 프로젝트)
            model_meta = meta.get('model')
            if model_meta:
                try:
                    obj = _import_model(
                        tmpdir, model_meta['file'], zf.read(model_meta['file']))
                except (RuntimeError, KeyError) as e:
                    self.report({'ERROR'}, str(e))
                    return {'CANCELLED'}
            else:
                obj = context.active_object
                if obj is None or obj.type != 'MESH':
                    self.report(
                        {'ERROR'}, "모델이 동봉되지 않은 프로젝트입니다 — 메시를 선택 후 실행하세요")
                    return {'CANCELLED'}

            # 2. Paint System 그룹 준비
            ps_ctx = self.parse_context(context)
            if ps_ctx.active_channel is None:
                bpy.ops.paint_system.new_group(template='BASIC')
                ps_ctx = self.parse_context(context)
            channel = ps_ctx.active_channel

            # 3. 레이어: 아래→위 순서로 읽어 스택 맨 위에 차례로 삽입
            count = 0
            for entry in meta.get('layers', []):
                file_key = entry.get('file')
                if not file_key:
                    continue
                try:
                    png = zf.read(file_key)
                except KeyError:
                    self.report({'WARNING'}, f"레이어 파일 누락: {file_key}")
                    continue
                png_path = os.path.join(tmpdir, os.path.basename(file_key))
                with open(png_path, 'wb') as f:
                    f.write(png)
                img = bpy.data.images.load(png_path)
                img.pack()  # 임시 파일이 사라져도 .blend에 유지
                img.name = entry.get('name', img.name)
                coord_type, uv_map_name = channel_coord_settings(context, channel)
                layer = channel.create_layer(
                    context, layer_name=entry.get('name', 'Layer'),
                    layer_type='IMAGE', image=img,
                    insert_at='TOP', update_active_index=False,
                    coord_type=coord_type, uv_map_name=uv_map_name)
                layer.enabled = bool(entry.get('visible', True))
                layer.blend_mode = _WEB_TO_PS_BLEND.get(
                    entry.get('blendMode', 'source-over'), 'MIX')
                _set_layer_opacity(layer, float(entry.get('opacity', 1.0)))
                count += 1

        self.report({'INFO'}, f".paint3d 가져오기 완료: 레이어 {count}개")
        return {'FINISHED'}


classes = (
    PAINTSYSTEM_OT_ImportPaint3D,
)

register, unregister = bpy.utils.register_classes_factory(classes)
