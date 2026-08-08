import numpy as np
from ...paintsystem.image import ImageTiles
from ...utils.imaging import gaussian_blur_alpha_safe


def _gaussian_blur_single(numpy_array, gaussian_sigma):
    """Apply gaussian blur to a single numpy array."""
    return gaussian_blur_alpha_safe(numpy_array, gaussian_sigma)

def gaussian_blur(image_tiles: ImageTiles, gaussian_sigma) -> ImageTiles:
    """
    Apply gaussian blur to ImageTiles.
    """
    blurred_tiles = {
        tile_num: _gaussian_blur_single(tile_array, gaussian_sigma)
        for tile_num, tile_array in image_tiles.tiles.items()
    }
    return ImageTiles(tiles=blurred_tiles, ori_path=image_tiles.ori_path, ori_packed=image_tiles.ori_packed)


def _sharpen_image_single(numpy_array, sharpen_amount):
    """Apply sharpen to a single numpy array."""
    array = np.clip(numpy_array, 0.0, 1.0).astype(np.float32, copy=False)
    blurred = gaussian_blur_alpha_safe(array, 1.0)
    sharpened = array + float(sharpen_amount) * (array - blurred)
    if array.ndim == 3 and array.shape[2] == 4:
        sharpened[..., 3] = array[..., 3]
    return np.clip(sharpened, 0.0, 1.0).astype(np.float32, copy=False)

def sharpen_image(image_tiles: ImageTiles, sharpen_amount) -> ImageTiles:
    """
    Apply sharpen to ImageTiles.
    """
    sharpened_tiles = {
        tile_num: _sharpen_image_single(tile_array, sharpen_amount)
        for tile_num, tile_array in image_tiles.tiles.items()
    }
    return ImageTiles(tiles=sharpened_tiles, ori_path=image_tiles.ori_path, ori_packed=image_tiles.ori_packed)


def _smooth_image_single(numpy_array, smooth_amount):
    """Apply smooth to a single numpy array."""
    sigma = max(float(smooth_amount), 0.0)
    sigma = 0.8 + sigma * 0.2
    return gaussian_blur_alpha_safe(numpy_array, sigma)

def smooth_image(image_tiles: ImageTiles, smooth_amount) -> ImageTiles:
    """
    Apply smooth to ImageTiles.
    """
    smoothed_tiles = {
        tile_num: _smooth_image_single(tile_array, smooth_amount)
        for tile_num, tile_array in image_tiles.tiles.items()
    }
    return ImageTiles(tiles=smoothed_tiles, ori_path=image_tiles.ori_path, ori_packed=image_tiles.ori_packed)
