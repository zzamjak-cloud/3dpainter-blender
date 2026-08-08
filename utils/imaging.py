"""이미지 리샘플링·필터 공용 구현 (순수 numpy, bpy 비의존)."""

import numpy as np


def gaussian_kernel_1d(sigma: float) -> np.ndarray:
    sigma = max(float(sigma), 1e-6)
    radius = max(1, int(sigma * 2.0))
    coords = np.arange(-radius, radius + 1, dtype=np.float32)
    kernel = np.exp(-0.5 * (coords / sigma) ** 2)
    kernel /= np.sum(kernel)
    return kernel.astype(np.float32)


def convolve1d_axis(array: np.ndarray, kernel: np.ndarray, axis: int) -> np.ndarray:
    radius = kernel.size // 2
    pad_width = [(0, 0)] * array.ndim
    pad_width[axis] = (radius, radius)
    padded = np.pad(array, pad_width, mode='edge')
    windows = np.lib.stride_tricks.sliding_window_view(padded, kernel.size, axis=axis)
    return np.tensordot(windows, kernel, axes=([-1], [0])).astype(np.float32, copy=False)


def gaussian_blur_array(array: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0:
        return array.astype(np.float32, copy=True)
    kernel = gaussian_kernel_1d(sigma)
    blurred = convolve1d_axis(array, kernel, axis=0)
    blurred = convolve1d_axis(blurred, kernel, axis=1)
    return blurred


def gaussian_blur_alpha_safe(numpy_array: np.ndarray, gaussian_sigma: float) -> np.ndarray:
    """RGBA는 알파 프리멀티플라이 후 블러 — 투명 픽셀의 색이 번지지 않게 한다."""
    array = np.clip(numpy_array, 0.0, 1.0).astype(np.float32, copy=False)
    if array.ndim != 3 or array.shape[2] != 4:
        return gaussian_blur_array(array, gaussian_sigma)

    alpha = array[..., 3:4]
    premult_rgb = array[..., :3] * alpha
    premult_rgba = np.concatenate((premult_rgb, alpha), axis=2)
    blurred = gaussian_blur_array(premult_rgba, gaussian_sigma)

    out_alpha = blurred[..., 3:4]
    safe_alpha = np.where(out_alpha > 1e-6, out_alpha, 1.0)
    out_rgb = blurred[..., :3] / safe_alpha
    out_rgb = np.where(out_alpha > 1e-6, out_rgb, 0.0)

    output = np.concatenate((out_rgb, out_alpha), axis=2)
    return np.clip(output, 0.0, 1.0).astype(np.float32, copy=False)


def bilinear_resize(src: np.ndarray, out_h: int, out_w: int) -> np.ndarray:
    """(sh, sw[, c]) → (out_h, out_w[, c]) 바이리니어 리사이즈. 2D/3D 모두 지원."""
    sh, sw = src.shape[:2]
    ys = np.linspace(0.0, sh - 1.0, out_h, dtype=np.float32)
    xs = np.linspace(0.0, sw - 1.0, out_w, dtype=np.float32)
    y0 = np.floor(ys).astype(np.int32)
    x0 = np.floor(xs).astype(np.int32)
    y1 = np.minimum(y0 + 1, sh - 1)
    x1 = np.minimum(x0 + 1, sw - 1)

    # 채널 축이 있으면 가중치에 길이 1 축을 덧붙여 브로드캐스트시킨다
    tail = (1,) * (src.ndim - 2)
    wy = (ys - y0).reshape((out_h, 1) + tail)
    wx = (xs - x0).reshape((1, out_w) + tail)

    wa = (1.0 - wy) * (1.0 - wx)
    wb = (1.0 - wy) * wx
    wc = wy * (1.0 - wx)
    wd = wy * wx

    ia = src[np.ix_(y0, x0)]
    ib = src[np.ix_(y0, x1)]
    ic = src[np.ix_(y1, x0)]
    id_ = src[np.ix_(y1, x1)]

    result = ia * wa + ib * wb + ic * wc + id_ * wd
    return result.astype(np.float32, copy=False)


def resize_mask_bilinear(mask: np.ndarray, out_h: int, out_w: int) -> np.ndarray:
    """2D 마스크 전용 리사이즈 — 동일 크기/퇴화 크기에 대한 예외 처리를 포함한다."""
    src_h, src_w = mask.shape
    if src_h == out_h and src_w == out_w:
        return mask.astype(np.float32, copy=True)
    if out_h <= 1 or out_w <= 1:
        return np.full((max(1, out_h), max(1, out_w)), float(mask.mean()), dtype=np.float32)
    return bilinear_resize(mask, out_h, out_w)


def rotate_mask_bilinear(mask: np.ndarray, angle_deg: float) -> np.ndarray:
    """2D 마스크를 중심 기준 회전. 원본 밖으로 나간 픽셀은 0으로 남긴다."""
    angle_rad = np.deg2rad(angle_deg)
    cos_v = float(np.cos(angle_rad))
    sin_v = float(np.sin(angle_rad))

    src_h, src_w = mask.shape
    cy = (src_h - 1) * 0.5
    cx = (src_w - 1) * 0.5

    corners = np.array([
        [-cy, -cx],
        [-cy, src_w - 1 - cx],
        [src_h - 1 - cy, -cx],
        [src_h - 1 - cy, src_w - 1 - cx],
    ], dtype=np.float32)

    rot_y = corners[:, 0] * cos_v - corners[:, 1] * sin_v
    rot_x = corners[:, 0] * sin_v + corners[:, 1] * cos_v
    out_h = int(np.ceil(rot_y.max() - rot_y.min() + 1.0))
    out_w = int(np.ceil(rot_x.max() - rot_x.min() + 1.0))
    out_h = max(out_h, 1)
    out_w = max(out_w, 1)

    oy = np.arange(out_h, dtype=np.float32) - (out_h - 1) * 0.5
    ox = np.arange(out_w, dtype=np.float32) - (out_w - 1) * 0.5
    grid_y, grid_x = np.meshgrid(oy, ox, indexing='ij')

    src_y = grid_y * cos_v + grid_x * sin_v + cy
    src_x = -grid_y * sin_v + grid_x * cos_v + cx

    valid = (src_y >= 0.0) & (src_y <= src_h - 1) & (src_x >= 0.0) & (src_x <= src_w - 1)

    y0 = np.floor(src_y).astype(np.int32)
    x0 = np.floor(src_x).astype(np.int32)
    y1 = np.minimum(y0 + 1, src_h - 1)
    x1 = np.minimum(x0 + 1, src_w - 1)

    wy = src_y - y0
    wx = src_x - x0

    wa = (1.0 - wy) * (1.0 - wx)
    wb = (1.0 - wy) * wx
    wc = wy * (1.0 - wx)
    wd = wy * wx

    rotated = np.zeros((out_h, out_w), dtype=np.float32)
    rotated[valid] = (
        mask[y0[valid], x0[valid]] * wa[valid]
        + mask[y0[valid], x1[valid]] * wb[valid]
        + mask[y1[valid], x0[valid]] * wc[valid]
        + mask[y1[valid], x1[valid]] * wd[valid]
    )
    return rotated
