# SPDX-License-Identifier: GPL-3.0-or-later
"""픽셀 스냅샷 기반 undo/redo 보완.

블렌더의 global(memfile) undo 스텝은 **이미지 픽셀 버퍼를 저장하지 않는다.**
그래서 numpy 로 픽셀을 직접 덮어쓰는 오퍼레이터(임포트·채우기·지우기·리사이즈·
필터)는 Ctrl+Z 로 되돌려도 픽셀이 그대로 남고, 대신 같은 스텝에 함께 담긴 다른
상태(레이어가 가리키는 이미지 포인터 등)만 되돌아간다. 사용자 눈에는 "이전
텍스처로 바뀌면서 칠한 게 날아간" 것처럼 보인다.

여기서는 픽셀을 바꾸기 직전/직후를 스냅샷해 두고, **씬 ID 프로퍼티에 심어 둔
토큰**이 undo 로 되돌아가는 것을 감지해 픽셀을 직접 복원한다. ID 프로퍼티는
memfile undo 에 함께 실리므로, 블렌더의 undo 스텝을 따로 추적하지 않고도
"지금 몇 번째 상태로 돌아왔는지"를 정확히 알 수 있다.

사용법::

    with pixel_undo_group([image]):
        write_rgba(image, new_pixels)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import bpy
import numpy as np

from ..utils.logging import get_logger

logger = get_logger(__name__)

# 씬에 심는 토큰 키. memfile undo 를 타고 함께 되돌아간다.
TOKEN_KEY = "ps_pixel_undo_token"

# 보관 한도 — 오래된 것부터 버린다.
MAX_ENTRIES = 8
MAX_BYTES = 256 * 1024 * 1024


@dataclass
class _ImageState:
    """이미지 한 장의 픽셀 스냅샷.

    8비트 이미지는 uint8 로 담아 메모리를 1/4 로 줄인다.
    """
    name: str
    width: int
    height: int
    is_float: bool
    data: np.ndarray

    @property
    def nbytes(self) -> int:
        return int(self.data.nbytes)


@dataclass
class _Entry:
    token: int
    before: list[_ImageState] = field(default_factory=list)
    after: list[_ImageState] = field(default_factory=list)

    @property
    def nbytes(self) -> int:
        return sum(s.nbytes for s in self.before) + sum(s.nbytes for s in self.after)


_entries: list[_Entry] = []
_next_token = 1
# 현재 이미지들에 올라가 있는 상태의 토큰
_applied_token = 0


def _capture(image) -> _ImageState | None:
    """이미지의 현재 픽셀을 스냅샷한다. 읽을 수 없으면 None."""
    if image is None or not image.has_data:
        return None
    try:
        width, height = int(image.size[0]), int(image.size[1])
        if width <= 0 or height <= 0:
            return None
        buf = np.empty(len(image.pixels), dtype=np.float32)
        image.pixels.foreach_get(buf)
    except (RuntimeError, ValueError, AttributeError):
        # UDIM 타일 이미지 등 pixels 로 접근할 수 없는 경우
        return None
    is_float = bool(image.is_float)
    data = buf if is_float else np.clip(buf * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return _ImageState(image.name, width, height, is_float, data)


def _restore(state: _ImageState) -> bool:
    """스냅샷을 이미지에 되돌린다. 대상이 없거나 크기가 달라졌으면 건너뛴다."""
    image = bpy.data.images.get(state.name)
    if image is None or not image.has_data:
        return False
    if int(image.size[0]) != state.width or int(image.size[1]) != state.height:
        # 리사이즈까지 되돌리면 다른 스텝과 충돌할 수 있어 크기부터 맞춘다
        try:
            image.scale(state.width, state.height)
        except RuntimeError:
            return False
    data = state.data
    flat = data if state.is_float else data.astype(np.float32) / 255.0
    try:
        image.pixels.foreach_set(np.ascontiguousarray(flat, dtype=np.float32))
        image.update()
        if hasattr(image, "update_tag"):
            image.update_tag()
    except (RuntimeError, ValueError):
        return False
    return True


def _trim() -> None:
    """보관 한도를 넘으면 오래된 항목부터 버린다."""
    while len(_entries) > MAX_ENTRIES:
        _entries.pop(0)
    total = sum(e.nbytes for e in _entries)
    while len(_entries) > 1 and total > MAX_BYTES:
        total -= _entries[0].nbytes
        _entries.pop(0)


class pixel_undo_group:
    """블록 안에서 바뀐 이미지 픽셀을 undo/redo 로 되돌릴 수 있게 기록한다.

    블록을 빠져나올 때 씬 토큰을 올려 두면, 오퍼레이터의 ``{'UNDO'}`` 가 밀어
    넣는 memfile 스텝에 그 토큰이 함께 실린다. 나중에 undo 로 토큰이 되돌아오는
    것을 보고 픽셀을 복원한다.
    """

    def __init__(self, images):
        self._images = [img for img in images if img is not None]
        self._entry = None

    def __enter__(self):
        global _next_token
        try:
            entry = _Entry(token=_next_token)
            for image in self._images:
                state = _capture(image)
                if state is not None:
                    entry.before.append(state)
            if entry.before:
                self._entry = entry
        except Exception:
            logger.debug("pixel undo capture failed", exc_info=True)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        global _next_token, _applied_token
        if self._entry is None or exc_type is not None:
            # 오퍼레이터가 실패했으면 기록하지 않는다
            return False
        try:
            for state in self._entry.before:
                image = bpy.data.images.get(state.name)
                after = _capture(image)
                if after is not None:
                    self._entry.after.append(after)
            _entries.append(self._entry)
            _applied_token = self._entry.token
            _next_token += 1
            _trim()
            scene = getattr(bpy.context, "scene", None)
            if scene is not None:
                scene[TOKEN_KEY] = self._entry.token
        except Exception:
            logger.debug("pixel undo commit failed", exc_info=True)
        return False


def sync_to_scene_token(*_args) -> None:
    """undo/redo 뒤에 씬 토큰을 보고 픽셀을 맞춘다.

    토큰이 내려갔으면 그 사이 항목들의 ``before`` 를 최신 것부터 되돌리고,
    올라갔으면 ``after`` 를 오래된 것부터 다시 적용한다.
    """
    global _applied_token
    try:
        scene = getattr(bpy.context, "scene", None)
        if scene is None or not _entries:
            return
        target = int(scene.get(TOKEN_KEY, 0) or 0)
        if target == _applied_token:
            return

        if target < _applied_token:
            for entry in sorted(_entries, key=lambda e: e.token, reverse=True):
                if target < entry.token <= _applied_token:
                    for state in entry.before:
                        _restore(state)
        else:
            for entry in sorted(_entries, key=lambda e: e.token):
                if _applied_token < entry.token <= target:
                    for state in entry.after:
                        _restore(state)
        _applied_token = target
    except Exception:
        logger.debug("pixel undo sync failed", exc_info=True)


def clear() -> None:
    """파일을 새로 열면 스냅샷은 의미가 없다."""
    global _applied_token
    _entries.clear()
    _applied_token = 0
