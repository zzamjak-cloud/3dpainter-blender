# SPDX-License-Identifier: GPL-3.0-or-later
"""numpy 로 직접 쓴 픽셀을 블렌더 undo 와 맞추는 모듈.

블렌더(5.2 ``image_undo.cc``) 이미지 undo 의 두 가지 성질이 문제의 뿌리다.

1. memfile undo 는 픽셀 버퍼를 담지 않는다. 채우기·병합·임포트처럼 numpy 로 직접
   쓴 픽셀은 Ctrl+Z 로 되돌아가지 않는다.
2. 스트로크 스텝은 **직전 IMAGE 스텝의 결과(post) 타일을 이미지 전체의 기준**으로
   재사용하고 실제 픽셀을 다시 읽지 않는다(memfile 스텝은 건너뛴다). 그래서 직접
   쓴 픽셀 위에 스트로크를 올린 뒤 그 스트로크를 undo/redo 하면 이미지 전체가
   옛 기준으로 덮어써진다 — 편집이 통째로 사라지거나 타일 단위로 섞이는 증상.

그래서 :class:`pixel_undo_group` 는 블록을 빠져나올 때 세 가지를 한다.

* 바꾼 이미지마다 같은 크기로 ``image.resize`` 를 실행해 **블렌더 자신의 IMAGE
  스텝**(전=직전 기준, 후=현재 픽셀)을 남긴다. 이 스텝이 오퍼레이터의 undo 단위가
  되고, 이후 스트로크의 기준도 올바르게 만든다. 이미지 하나당 스텝 하나다.
* 직전/직후 픽셀을 스냅샷하고 **씬 ID 프로퍼티 토큰**을 올린다. 블렌더 스텝의
  "전" 이 실제와 다를 수 있는 경우(그 이미지에 IMAGE 스텝이 아직 없을 때)와
  새로 만든 이미지의 redo(빈 채로 되살아남)를 토큰 변화를 보고 직접 복원한다.
* 더미 ID 를 만들고 지워 다음 undo push 때 memfile 스냅샷이 꼭 기록되게 한다 —
  블렌더는 ID 를 만들거나 지웠을 때만 memfile 을 다시 쓰므로, 그러지 않으면 토큰이
  undo 에 실리지 않는다.

**호출하는 오퍼레이터는 ``'UNDO'`` 를 빼야 한다.** IMAGE 스텝이 이미 undo 단위이므로
오퍼레이터 자체의 push 는 헛도는 스텝을 하나 더 만든다. 레이어 생성·삭제 같은 ID
변경은 IMAGE 스텝을 push 할 때 자동으로 딸려 붙는 memfile 스냅샷에 담기므로, 픽셀을
쓰기 **전에** 끝내 둔다. 새 이미지만 만들고 기존 이미지를 바꾸지 않은 오퍼레이터는
IMAGE 스텝이 없으니 :func:`push_undo_step` 으로 스텝을 직접 남긴다.

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
MAX_ENTRIES = 32
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
    if image is None:
        return False
    if not image.has_data:
        # memfile redo 로 되살아난 생성(GENERATED) 이미지는 버퍼가 아직 없다 —
        # pixels 에 접근하면 블렌더가 버퍼를 만들어 준다
        try:
            len(image.pixels)
        except (RuntimeError, AttributeError):
            return False
        if not image.has_data:
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

    def __init__(self, images, created=()):
        self._images = [img for img in images if img is not None]
        # 블록을 나올 때 남긴 블렌더 IMAGE 스텝 수 — 0 이면 호출자가 push_undo_step 필요
        self.registered = 0
        # 블록 안에서 새로 만든 이미지 — before 는 없고 after 만 기록한다.
        # undo 로 데이터블록이 사라졌다가 redo 로 다시 생기면 빈 이미지가 되므로
        # 그때 after 를 덮어써 픽셀을 되살린다.
        self._created = [img for img in created if img is not None]
        self._entry = None

    def add(self, image) -> None:
        """블록 안에서 새로 만든 이미지를 redo 복원 대상으로 등록한다."""
        if image is not None:
            self._created.append(image)

    def __enter__(self):
        global _next_token
        try:
            # 씬에 남아 있는 토큰(이전 세션에서 저장된 파일 등)보다 항상 커야 한다 —
            # 작으면 undo/redo 방향 판정이 뒤집혀 복원이 엇갈린다
            _next_token = max(_next_token, _scene_token() + 1)
            entry = _Entry(token=_next_token)
            for image in self._images:
                state = _capture(image)
                if state is not None:
                    entry.before.append(state)
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
            seen = set()
            for image in [*self._images, *self._created]:
                if image is None or image.name in seen:
                    continue
                seen.add(image.name)
                after = _capture(image)
                if after is not None:
                    self._entry.after.append(after)
            if not self._entry.before and not self._entry.after:
                return False
            # undo 로 되돌아간 뒤 새 작업을 하면 그 뒤의 redo 분기는 블렌더가 버린다.
            # 그 분기의 스냅샷을 남겨 두면 다음 undo 때 토큰 범위에 걸려 옛 픽셀이
            # 덮어써지므로(편집 내역이 통째로 사라지는 증상) 여기서 함께 버린다.
            _entries[:] = [e for e in _entries if e.token <= _applied_token]
            _entries.append(self._entry)
            _applied_token = self._entry.token
            _next_token += 1
            _trim()
            scene = getattr(bpy.context, "scene", None)
            if scene is not None:
                scene[TOKEN_KEY] = self._entry.token
            _mark_main_changed()
            self.registered = register_image_undo(self._images)
        except Exception:
            logger.debug("pixel undo commit failed", exc_info=True)
        return False


def register_image_undo(images) -> int:
    """이미지들의 현재 픽셀을 블렌더 IMAGE undo 스텝으로 등록한다. 반환: 스텝 수.

    같은 크기 ``image.resize`` 는 픽셀을 바꾸지 않으면서 전(직전 IMAGE 스텝 기준)/
    후(현재 픽셀) 타일을 담은 스텝을 남긴다 — 2K 약 12ms, 4K 약 70ms.
    """
    count = 0
    seen = set()
    for image in images:
        if image is None or image.name in seen or not image.has_data:
            continue
        seen.add(image.name)
        width, height = int(image.size[0]), int(image.size[1])
        if width <= 0 or height <= 0:
            continue
        try:
            with bpy.context.temp_override(edit_image=image):
                result = bpy.ops.image.resize(size=(width, height))
        except Exception:
            logger.debug("register_image_undo failed for %s", image.name, exc_info=True)
            continue
        if 'FINISHED' in result:
            count += 1
    return count


def _mark_main_changed() -> None:
    """다음 undo push 때 memfile 스냅샷이 꼭 기록되게 한다.

    블렌더는 ID 를 만들거나 지웠을 때만 ``is_memfile_undo_written`` 을 내려 memfile
    을 다시 쓴다. 픽셀과 씬 토큰만 바뀐 경우 스냅샷이 생략되어 토큰이 undo 에
    실리지 않으므로, 더미 텍스트 ID 를 만들고 바로 지워 플래그를 내린다.
    """
    try:
        text = bpy.data.texts.new(".ps_pixel_undo_touch")
        bpy.data.texts.remove(text)
    except Exception:
        logger.debug("mark main changed failed", exc_info=True)


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


def push_undo_step(message: str) -> None:
    """오퍼레이터 밖(타이머 등)에서 픽셀을 바꿨을 때 undo 스텝을 직접 남긴다."""
    try:
        bpy.ops.ed.undo_push(message=message)
    except RuntimeError:
        logger.debug("undo_push failed", exc_info=True)


def _scene_token() -> int:
    scene = getattr(bpy.context, "scene", None)
    if scene is None:
        return 0
    try:
        return int(scene.get(TOKEN_KEY, 0) or 0)
    except (TypeError, ValueError):
        return 0


def clear() -> None:
    """파일을 새로 열면 스냅샷은 의미가 없다.

    씬에 저장돼 있던 토큰을 현재 상태의 기준으로 삼고 카운터를 그 위로 올린다.
    """
    global _applied_token, _next_token
    _entries.clear()
    _applied_token = _scene_token()
    _next_token = max(_next_token, _applied_token + 1)
