# SPDX-License-Identifier: GPL-3.0-or-later
# 3DPainter 포크 추가 기능: PSD 왕복 연동 (psd-tools)
#
# 정책: 픽셀 레이어만 왕복한다. 포토샵 전용 기능(조정 레이어·텍스트·
# 스마트 오브젝트·레이어 스타일)은 읽을 때 무시되고 다시 쓸 때 보존되지
# 않는다 — 정교한 보정은 포토샵에서, 페인팅은 블렌더에서.

import os
import sys

import numpy as np

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Operator

from .common import PSContextMixin
from ..paintsystem.image import read_rgba, write_rgba
from ..paintsystem.pixel_undo import pixel_undo_group, push_undo_step
from ..utils.registration import collect_classes

# Paint System(MixRGB 계열) ↔ PSD 블렌드 모드 매핑
_PS_TO_PSD = {
    'MIX': 'normal', 'MULTIPLY': 'multiply', 'SCREEN': 'screen',
    'OVERLAY': 'overlay', 'DARKEN': 'darken', 'LIGHTEN': 'lighten',
    'BURN': 'color burn', 'DODGE': 'color dodge', 'ADD': 'linear dodge',
    'DIFFERENCE': 'difference', 'EXCLUSION': 'exclusion',
    'SUBTRACT': 'subtract', 'DIVIDE': 'divide', 'HUE': 'hue',
    'SATURATION': 'saturation', 'COLOR': 'color', 'VALUE': 'luminosity',
    'SOFT_LIGHT': 'soft light', 'LINEAR_LIGHT': 'linear light',
}
_PSD_TO_PS_EXTRA = {
    'hard light': 'OVERLAY', 'linear burn': 'BURN',
    'vivid light': 'LINEAR_LIGHT', 'pass through': 'MIX',
}

KEY_PSD_PATH = "ps_psd_path"

# 라이브 동기화 상태 (세션 한정)
_sync_state = {"running": False, "mtime": 0.0}


def _resync_extension_wheels() -> bool:
    """공용 .local/site-packages 휠을 Blender 내부 동기화로 복구한다.

    `--factory-startup` 등 확장이 비활성인 채로 뜬 Blender 가 공용 휠
    디렉터리를 비워 버리는 경우가 있어, 재설치 없이 되살리기 위함.
    """
    try:
        import importlib
        from bl_pkg import bl_extension_ops as ext_ops
        enabled = set()
        for name in bpy.context.preferences.addons.keys():
            parts = name.split('.')
            if len(parts) == 3 and parts[0] == 'bl_ext':
                enabled.add((parts[1], parts[2]))
        errors = []
        ext_ops._extensions_repo_sync_wheels(
            ext_ops.repo_cache_store_ensure(), enabled,
            error_fn=lambda ex: errors.append(ex),
        )
        importlib.invalidate_caches()
        if errors:
            print("[3DPainter] 휠 동기화 오류:", errors)
        return True
    except Exception as ex:
        print("[3DPainter] 휠 동기화 실패:", ex)
        return False


def _require_psd_tools():
    try:
        from psd_tools import PSDImage  # noqa: F401
        return None
    except ImportError:
        pass
    if _resync_extension_wheels():
        try:
            from psd_tools import PSDImage  # noqa: F401
            return None
        except ImportError:
            pass
    return "psd-tools를 불러올 수 없습니다 — 블렌더 재시작 후에도 같으면 확장을 zip으로 재설치하세요"


def _blend_by_name(name: str):
    from psd_tools.constants import BlendMode
    for mode in BlendMode:
        if mode.name.replace('_', ' ').lower() == name:
            return mode
    return BlendMode.NORMAL


def _psd_to_ps_blend(psd_mode) -> str:
    name = psd_mode.name.replace('_', ' ').lower()
    for ps, psd_name in _PS_TO_PSD.items():
        if psd_name == name:
            return ps
    return _PSD_TO_PS_EXTRA.get(name, 'MIX')


def channel_coord_settings(context, channel) -> tuple[str, str]:
    """새 레이어에 쓸 (coord_type, uv_map_name) — 기존 형제 레이어를 따라간다.

    빈 uv_map_name으로 레이어를 만들면 표시(렌더 UV)와 페인팅(활성 UV)이
    어긋나 스트로크가 아일랜드 수만큼 복제돼 보이는 문제가 있다.
    """
    for l in channel.flattened_layers:
        if l.type == 'IMAGE' and l.uv_map_name:
            return l.coord_type, l.uv_map_name
    ps_obj = PSContextMixin.parse_context(context).ps_object
    if ps_obj and ps_obj.type == 'MESH' and ps_obj.data.uv_layers:
        uv = ps_obj.data.uv_layers.active or ps_obj.data.uv_layers[0]
        return 'UV', uv.name
    return 'UV', ""


def _image_layers_top_down(channel):
    """채널의 이미지 레이어를 UI(위→아래) 순서로 반환한다."""
    return [
        l for l in channel.flattened_layers
        if l.type == 'IMAGE' and l.image is not None
    ]


def _image_to_uint8(img) -> np.ndarray:
    """블렌더 이미지 → (H, W, 4) uint8, PSD의 top-down 행 순서로 뒤집는다."""
    arr = np.flipud(read_rgba(img))
    return np.clip(arr * 255.0 + 0.5, 0, 255).astype(np.uint8)


def _uint8_to_image(img, arr: np.ndarray) -> None:
    """(H, W, 4) uint8(top-down) → 블렌더 이미지 픽셀."""
    write_rgba(img, np.flipud(arr.astype(np.float32) / 255.0))


def _psd_layer_canvas_pixels(psd, layer) -> np.ndarray:
    """PSD 레이어 픽셀을 캔버스 크기 (H, W, 4) uint8로 합성(오프셋 반영)."""
    w, h = psd.size
    canvas = np.zeros((h, w, 4), dtype=np.uint8)
    data = layer.numpy()  # (lh, lw, c) float32 0~1
    if data is None or data.size == 0:
        return canvas
    if data.shape[2] == 3:
        data = np.concatenate(
            [data, np.ones((*data.shape[:2], 1), dtype=data.dtype)], axis=2)
    lh, lw = data.shape[:2]
    x0, y0 = max(layer.left, 0), max(layer.top, 0)
    x1, y1 = min(layer.left + lw, w), min(layer.top + lh, h)
    if x1 <= x0 or y1 <= y0:
        return canvas
    sx, sy = x0 - layer.left, y0 - layer.top
    crop = data[sy:sy + (y1 - y0), sx:sx + (x1 - x0), :4]
    canvas[y0:y1, x0:x1] = np.clip(crop * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return canvas


def _layer_has_content(image) -> bool:
    """이미지에 실제로 칠해진 픽셀(알파 > 0)이 있는지.

    메모리 때문에 배열을 캐시하지 않고 알파만 확인하고 버린다 — 내보내기는
    자주 하는 작업이 아니라, 레이어를 두 번 읽는 비용보다 8K 텍스처 여러 장을
    동시에 들고 있는 쪽이 위험하다.
    """
    try:
        return bool(np.any(read_rgba(image)[..., 3] > 0.0))
    except (RuntimeError, ValueError):
        return True


def _export_canvas_size(layers) -> tuple[int, int]:
    """내보낼 PSD 문서 크기. **내용이 있는 레이어**만으로 정한다.

    그룹 생성 시 딸려오는 빈 2048 레이어가 max 를 끌어올려, 1024 텍스처를
    가져왔는데도 문서가 2048 로 나가던 문제를 막는다.
    """
    sized = [l for l in layers if _layer_has_content(l.image)] or list(layers)
    width = max(int(l.image.size[0]) for l in sized)
    height = max(int(l.image.size[1]) for l in sized)
    return width, height


def _set_layer_opacity(layer, value: float) -> None:
    try:
        layer.pre_mix_node.inputs['Opacity'].default_value = value
    except (AttributeError, KeyError):
        pass


class PAINTSYSTEM_OT_ExportPSD(PSContextMixin, Operator):
    """활성 채널의 레이어 스택을 PSD 파일로 내보낸다"""
    bl_idname = "paint_system.export_psd"
    bl_label = "Export PSD"
    bl_options = {'REGISTER'}

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default='*.psd', options={'HIDDEN'})

    canvas_size: EnumProperty(
        name="Canvas Size",
        description="PSD 문서 크기를 정하는 방식",
        items=[
            ('AUTO', "Auto", "내용이 있는 레이어 중 가장 큰 크기를 사용"),
            ('CUSTOM', "Custom", "크기를 직접 지정"),
        ],
        default='AUTO',
    )
    canvas_width: IntProperty(
        name="Width", default=1024, min=1, subtype='PIXEL')
    canvas_height: IntProperty(
        name="Height", default=1024, min=1, subtype='PIXEL')

    @classmethod
    def poll(cls, context):
        ps_ctx = cls.parse_context(context)
        return ps_ctx.active_channel is not None

    def invoke(self, context, event):
        if not self.filepath:
            self.filepath = context.scene.get(KEY_PSD_PATH, "untitled.psd")
        # Custom 필드 초기값은 픽셀을 읽지 않고 정할 수 있는 최대 레이어 크기로
        layers = _image_layers_top_down(self.parse_context(context).active_channel)
        if layers:
            self.canvas_width = max(int(l.image.size[0]) for l in layers)
            self.canvas_height = max(int(l.image.size[1]) for l in layers)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "canvas_size")
        if self.canvas_size == 'CUSTOM':
            col.prop(self, "canvas_width")
            col.prop(self, "canvas_height")

    def execute(self, context):
        err = _require_psd_tools()
        if err:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}
        from psd_tools import PSDImage
        from psd_tools.api.layers import PixelLayer
        from PIL import Image

        ps_ctx = self.parse_context(context)
        layers = _image_layers_top_down(ps_ctx.active_channel)
        if not layers:
            self.report({'ERROR'}, "내보낼 이미지 레이어가 없습니다")
            return {'CANCELLED'}

        if self.canvas_size == 'CUSTOM':
            w, h = int(self.canvas_width), int(self.canvas_height)
        else:
            w, h = _export_canvas_size(layers)
        psd = PSDImage.new(mode='RGBA', size=(w, h))

        # PSD 내부 리스트는 아래→위 순서: 스택의 맨 아래 레이어부터 append
        for layer in reversed(layers):
            arr = _image_to_uint8(layer.image)
            pil = Image.fromarray(arr, mode='RGBA')
            pixel_layer = PixelLayer.frompil(pil, psd, layer.layer_name, 0, 0)
            pixel_layer.opacity = int(round(max(0.0, min(1.0, layer.opacity)) * 255))
            pixel_layer.visible = bool(layer.enabled)
            pixel_layer.blend_mode = _blend_by_name(
                _PS_TO_PSD.get(layer.blend_mode, 'normal'))
            psd.append(pixel_layer)

        path = bpy.path.abspath(self.filepath)
        if not path.lower().endswith('.psd'):
            path += '.psd'
        psd.save(path)
        context.scene[KEY_PSD_PATH] = path
        _sync_state["mtime"] = os.path.getmtime(path)
        self.report({'INFO'}, f"PSD 내보내기 완료: {os.path.basename(path)} ({w}x{h})")
        return {'FINISHED'}


def _clear_channel_layers(context, channel) -> int:
    """채널의 레이어를 전부 지운다 (폴더는 자식까지). 반환: 지운 최상위 항목 수.

    이미지 데이터블록은 지우지 않고 고아로 남긴다 — memfile undo 가 되살릴 때
    픽셀 캐시가 그대로 있어야 하고, 저장·재로드 시 블렌더가 알아서 정리한다.
    """
    roots = [l for l in channel.flattened_unlinked_layers if int(l.parent_id) == -1]
    if roots:
        channel.delete_layers(context, roots)
    return len(roots)


def _import_psd_into_channel(context, channel, path, create_missing=True,
                             replace=False) -> tuple[int, int]:
    """PSD 픽셀 레이어를 채널에 반영한다. 이름이 같으면 픽셀 갱신, 없으면 생성.

    ``replace=True`` 면 기존 레이어를 모두 지운 뒤 PSD 스택으로 통째로 교체한다.
    레이어 생성·활성 변경(ID 변경)을 모두 끝낸 뒤 기존 이미지 덮어쓰기를 하는 순서다 —
    덮어쓰기가 남기는 IMAGE undo 스텝의 memfile 스냅샷에 ID 변경이 함께 담기게 한다.
    반환: (반영한 레이어 수, 남긴 IMAGE undo 스텝 수). 스텝이 0 이면 호출자가
    :func:`push_undo_step` 으로 스텝을 남겨야 한다.
    """
    from psd_tools import PSDImage

    psd = PSDImage.open(path)
    w, h = psd.size
    if replace:
        _clear_channel_layers(context, channel)
    # 이름 중복을 허용하기 위해 이름→레이어 목록으로 매칭하고, 매칭 시 소비한다
    existing: dict[str, list] = {}
    for l in _image_layers_top_down(channel):
        existing.setdefault(l.layer_name, []).append(l)

    count = 0
    last_layer = None
    overwrites: list[tuple] = []   # (레이어, 이미지, 픽셀) — 마지막에 한꺼번에 쓴다
    # list(psd)는 아래→위 순서 — 아래부터 처리하며 새 레이어는 스택 위에 쌓는다
    for psd_layer in list(psd):
        if psd_layer.is_group() or psd_layer.kind != 'pixel':
            continue  # v1: 픽셀 레이어만 (그룹/조정/텍스트 무시)
        arr = _psd_layer_canvas_pixels(psd, psd_layer)
        matches = existing.get(psd_layer.name)
        ps_layer = matches.pop(0) if matches else None
        if ps_layer is not None:
            overwrites.append((ps_layer, ps_layer.image, arr))
        elif create_missing:
            img = bpy.data.images.new(psd_layer.name, width=w, height=h, alpha=True)
            with pixel_undo_group([], created=[img]):
                _uint8_to_image(img, arr)
            coord_type, uv_map_name = channel_coord_settings(context, channel)
            ps_layer = channel.create_layer(
                context, layer_name=psd_layer.name, layer_type='IMAGE',
                image=img, insert_at='TOP', update_active_index=False,
                coord_type=coord_type, uv_map_name=uv_map_name)
        else:
            continue
        ps_layer.enabled = bool(psd_layer.visible)
        ps_layer.blend_mode = _psd_to_ps_blend(psd_layer.blend_mode)
        _set_layer_opacity(ps_layer, psd_layer.opacity / 255.0)
        last_layer = ps_layer
        count += 1
    if replace and last_layer is not None:
        # 교체 직후엔 이전 active_index 가 무의미하므로 맨 위 레이어를 활성으로
        channel.set_active_index_to_layer(context, last_layer)

    steps = 0
    for _layer, img, arr in overwrites:
        # 기존 레이어를 덮어쓰므로 IMAGE 스텝+스냅샷을 남긴다 (이미지마다 스텝 하나)
        with pixel_undo_group([img]) as group:
            if int(img.size[0]) != w or int(img.size[1]) != h:
                img.scale(w, h)
            _uint8_to_image(img, arr)
        steps += group.registered
    return count, steps


def _import_image_into_channel(context, channel, path, create_missing=True) -> int:
    """일반 이미지 파일(PNG 등)을 채널의 이미지 레이어로 가져온다.

    PSD 임포트와 똑같이 **내부 이미지에 픽셀을 복사**한다. 파일을 그대로 링크하면
    레이어 이미지가 디스크 파일에 묶여, 페인팅이 원본을 건드리고 스포이드·병합처럼
    픽셀을 직접 읽는 경로가 PSD 레이어와 다르게 동작한다.

    반환: (반영한 레이어 수 0 또는 1, 남긴 IMAGE undo 스텝 수).
    """
    src = bpy.data.images.load(path, check_existing=False)
    try:
        width, height = int(src.size[0]), int(src.size[1])
        if width <= 0 or height <= 0 or not src.has_data:
            raise RuntimeError("이미지 픽셀을 읽을 수 없습니다")
        pixels = read_rgba(src)
        is_float = bool(src.is_float)
        colorspace = src.colorspace_settings.name
        alpha_mode = src.alpha_mode
    finally:
        bpy.data.images.remove(src)

    name = os.path.splitext(os.path.basename(path))[0]
    # PSD 임포트와 같은 규칙: 이름이 같은 이미지 레이어가 있으면 픽셀만 갱신한다
    target = next(
        (l for l in _image_layers_top_down(channel) if l.layer_name == name), None)
    if target is not None:
        img = target.image
        # 기존 레이어를 덮어쓰므로 IMAGE 스텝+스냅샷을 남긴다
        with pixel_undo_group([img]) as group:
            if int(img.size[0]) != width or int(img.size[1]) != height:
                img.scale(width, height)
            write_rgba(img, pixels)
        return 1, group.registered
    if not create_missing:
        return 0, 0

    img = bpy.data.images.new(
        name, width=width, height=height, alpha=True, float_buffer=is_float)
    # 원본과 픽셀 해석(색공간·알파)을 맞춰야 복사한 버퍼가 같은 색으로 보인다
    try:
        img.colorspace_settings.name = colorspace
    except (TypeError, RuntimeError):
        pass
    try:
        img.alpha_mode = alpha_mode
    except (TypeError, RuntimeError):
        pass
    with pixel_undo_group([], created=[img]):
        write_rgba(img, pixels)
    coord_type, uv_map_name = channel_coord_settings(context, channel)
    channel.create_layer(
        context, layer_name=name, layer_type='IMAGE', image=img,
        insert_at='TOP', update_active_index=True,
        coord_type=coord_type, uv_map_name=uv_map_name)
    return 1, 0


# PSD 외에 레이어로 가져올 수 있는 이미지 확장자 (블렌더가 읽을 수 있는 것들)
_IMAGE_EXTENSIONS = (
    '.png', '.jpg', '.jpeg', '.tga', '.tif', '.tiff',
    '.bmp', '.exr', '.hdr', '.webp',
)
_IMPORT_FILTER_GLOB = ';'.join(['*.psd', *(f'*{ext}' for ext in _IMAGE_EXTENSIONS)])


class PAINTSYSTEM_OT_ImportPSD(PSContextMixin, Operator):
    """PSD 또는 이미지 파일(PNG 등)을 활성 채널의 레이어로 가져온다"""
    bl_idname = "paint_system.import_psd"
    bl_label = "Import PSD / Image"
    # undo 단위: 기존 이미지를 덮어썼으면 pixel_undo_group 의 IMAGE 스텝, 새 레이어만
    # 만들었으면 push_undo_step 의 memfile 스텝 — 'UNDO' 는 헛도는 스텝을 더 만든다
    bl_options = {'REGISTER'}

    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default=_IMPORT_FILTER_GLOB, options={'HIDDEN'})

    import_mode: EnumProperty(
        name="Import Mode",
        description="PSD 레이어를 기존 채널에 반영하는 방식",
        items=[
            ('REPLACE', "Replace",
             "채널의 기존 레이어를 모두 지우고 PSD 레이어 스택으로 통째로 교체"),
            ('MERGE', "Merge",
             "이름이 같은 레이어는 픽셀만 갱신하고 없는 레이어는 위에 추가"),
        ],
        default='REPLACE',
    )

    @classmethod
    def poll(cls, context):
        ps_ctx = cls.parse_context(context)
        return ps_ctx.active_channel is not None

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def draw(self, context):
        col = self.layout.column()
        col.label(text="PSD")
        col.prop(self, "import_mode", expand=True)
        col.label(text="PNG 등 단일 이미지는 항상 레이어로 추가됩니다", icon='INFO')

    def execute(self, context):
        path = bpy.path.abspath(self.filepath)
        if not os.path.isfile(path):
            self.report({'ERROR'}, "파일을 찾을 수 없습니다")
            return {'CANCELLED'}
        ext = os.path.splitext(path)[1].lower()
        ps_ctx = self.parse_context(context)

        if ext != '.psd':
            if ext not in _IMAGE_EXTENSIONS:
                self.report({'ERROR'}, f"지원하지 않는 파일 형식입니다: {ext or '(없음)'}")
                return {'CANCELLED'}
            try:
                count, steps = _import_image_into_channel(
                    context, ps_ctx.active_channel, path)
            except RuntimeError as exc:
                self.report({'ERROR'}, f"이미지를 가져오지 못했습니다: {exc}")
                return {'CANCELLED'}
            if steps == 0:
                push_undo_step("Import Image")
            self.report({'INFO'}, f"이미지 가져오기 완료: 레이어 {count}개")
            return {'FINISHED'}

        err = _require_psd_tools()
        if err:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}
        # 라이브 동기화는 PSD 전용이므로 PSD 를 가져왔을 때만 경로를 기억한다
        # (undo 스텝에 함께 담기도록 픽셀 쓰기보다 먼저 기록)
        context.scene[KEY_PSD_PATH] = path
        _sync_state["mtime"] = os.path.getmtime(path)
        count, steps = _import_psd_into_channel(
            context, ps_ctx.active_channel, path,
            replace=(self.import_mode == 'REPLACE'))
        if steps == 0:
            push_undo_step("Import PSD")
        how = "교체" if self.import_mode == 'REPLACE' else "병합"
        self.report({'INFO'}, f"PSD 가져오기 완료({how}): 레이어 {count}개")
        return {'FINISHED'}


class PAINTSYSTEM_OT_OpenPSDInPhotoshop(Operator):
    """현재 연동된 PSD 파일을 포토샵에서 연다"""
    bl_idname = "paint_system.open_psd_in_photoshop"
    bl_label = "Open PS"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        path = context.scene.get(KEY_PSD_PATH)
        if not path:
            cls.poll_message_set("먼저 Export 또는 Import로 PSD를 연동하세요")
            return False
        if not os.path.isfile(bpy.path.abspath(path)):
            cls.poll_message_set(f"연동된 PSD 파일이 없습니다: {path}")
            return False
        return True

    def execute(self, context):
        import subprocess
        import sys
        path = bpy.path.abspath(context.scene.get(KEY_PSD_PATH))
        try:
            if sys.platform == 'darwin':
                # 포토샵 지정 실행, 미설치 등 실패 시 기본 연결 앱으로 폴백
                r = subprocess.run(
                    ['open', '-a', 'Adobe Photoshop', path], capture_output=True)
                if r.returncode != 0:
                    subprocess.run(['open', path])
            elif sys.platform.startswith('win'):
                os.startfile(path)  # 기본 연결 프로그램 (보통 포토샵)
            else:
                subprocess.run(['xdg-open', path])
        except Exception as e:
            self.report({'ERROR'}, f"파일을 열 수 없습니다: {e}")
            return {'CANCELLED'}
        return {'FINISHED'}


def _sync_timer():
    """PSD 파일 변경 감시 — 포토샵에서 저장하면 이름이 같은 레이어 픽셀 갱신."""
    if not _sync_state["running"]:
        return None  # 타이머 종료
    scene = bpy.context.scene
    path = scene.get(KEY_PSD_PATH)
    if not path or not os.path.isfile(path):
        return 2.0
    try:
        mtime = os.path.getmtime(path)
        if mtime > _sync_state["mtime"] + 1e-4:
            _sync_state["mtime"] = mtime
            from .common import PSContextMixin as _mix
            ps_ctx = _mix.parse_context(bpy.context)
            if ps_ctx.active_channel is not None:
                # 타이머 컨텍스트에서는 레이어 생성 없이 픽셀만 갱신 (안전)
                # 덮어쓴 이미지마다 IMAGE undo 스텝이 남는다
                _import_psd_into_channel(
                    bpy.context, ps_ctx.active_channel, path, create_missing=False)
    except Exception:
        pass
    return 2.0


class PAINTSYSTEM_OT_TogglePSDSync(Operator):
    """PSD 라이브 동기화를 켜거나 끈다 (포토샵 저장 → 자동 반영)"""
    bl_idname = "paint_system.toggle_psd_sync"
    bl_label = "Toggle PSD Live Sync"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return bool(context.scene.get(KEY_PSD_PATH))

    def execute(self, context):
        if _sync_state["running"]:
            _sync_state["running"] = False
            self.report({'INFO'}, "PSD 동기화 중지")
        else:
            err = _require_psd_tools()
            if err:
                self.report({'ERROR'}, err)
                return {'CANCELLED'}
            _sync_state["running"] = True
            path = context.scene.get(KEY_PSD_PATH)
            if path and os.path.isfile(path):
                _sync_state["mtime"] = os.path.getmtime(path)
            bpy.app.timers.register(_sync_timer, first_interval=2.0)
            self.report({'INFO'}, "PSD 동기화 시작 (2초 간격 감시)")
        return {'FINISHED'}


def is_sync_running() -> bool:
    return _sync_state["running"]


classes = collect_classes(sys.modules[__name__])


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    _sync_state["running"] = False
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
