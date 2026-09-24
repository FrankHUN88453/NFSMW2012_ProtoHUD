"""Small numpy BC3 (DXT5) encoder for UI textures (bounding-box endpoints, nearest-index fit).

Retail UI textures are almost all BC3; storing the prototype's A8R8G8B8 textures uncompressed quadruples
their memory footprint, so they are re-encoded here.
"""
import numpy as np


def _blocks(img):
    h, w, _ = img.shape
    ph, pw = (-h) % 4, (-w) % 4
    if ph or pw:
        img = np.pad(img, ((0, ph), (0, pw), (0, 0)), mode='edge')
    H, W = img.shape[0] // 4, img.shape[1] // 4
    return img.reshape(H, 4, W, 4, 4).transpose(0, 2, 1, 3, 4).reshape(H * W, 16, 4).astype(np.int32), H, W


def _to565(c):
    return ((c[:, 0] >> 3) << 11) | ((c[:, 1] >> 2) << 5) | (c[:, 2] >> 3)


def _from565(v):
    r = ((v >> 11) & 31) * 255 // 31
    g = ((v >> 5) & 63) * 255 // 63
    b = (v & 31) * 255 // 31
    return np.stack([r, g, b], -1)


def encode_bc3(rgba):
    """rgba: HxWx4 uint8 -> BC3 bytes (block rows, top to bottom)."""
    blk, H, W = _blocks(rgba)
    n = len(blk)
    # --- alpha: 8-level mode with a0 > a1 --------------------------------------
    a = blk[:, :, 3]
    a0 = a.max(1)
    a1 = a.min(1)
    same = a0 == a1
    a0 = np.where(same & (a0 < 255), a0 + 1, a0)
    a1 = np.where(same & (a0 == 255), a1 - 1, a1)
    a1 = np.clip(a1, 0, 255)
    k = np.arange(8)
    w0 = np.array([7, 0, 6, 5, 4, 3, 2, 1])
    w1 = np.array([0, 7, 1, 2, 3, 4, 5, 6])
    apal = (w0[None, :] * a0[:, None] + w1[None, :] * a1[:, None]) // 7          # n,8
    aidx = np.abs(a[:, :, None] - apal[:, None, :]).argmin(2).astype(np.int64)    # n,16
    abits = np.zeros(n, np.int64)
    for i in range(16):
        abits |= aidx[:, i] << (3 * i)
    # --- colour: always 4-colour mode in BC3 -----------------------------------
    rgb = blk[:, :, :3]
    weight = (blk[:, :, 3] > 8)[:, :, None]            # ignore fully transparent texels for endpoints
    big = np.where(weight, rgb, -1)
    small = np.where(weight, rgb, 256)
    cmax = big.max(1)
    cmin = small.min(1)
    none = (cmax < 0).any(1)
    cmax = np.where(none[:, None], 0, cmax)
    cmin = np.where(none[:, None], 0, cmin)
    c0 = _to565(np.clip(cmax, 0, 255))
    c1 = _to565(np.clip(cmin, 0, 255))
    p0, p1 = _from565(c0), _from565(c1)
    cpal = np.stack([p0, p1, (2 * p0 + p1) // 3, (p0 + 2 * p1) // 3], 1)          # n,4,3
    d = ((rgb[:, :, None, :] - cpal[:, None, :, :]) ** 2).sum(3)                   # n,16,4
    cidx = d.argmin(2).astype(np.int64)
    cbits = np.zeros(n, np.int64)
    for i in range(16):
        cbits |= cidx[:, i] << (2 * i)
    out = np.zeros((n, 16), np.uint8)
    out[:, 0] = a0
    out[:, 1] = a1
    for i in range(6):
        out[:, 2 + i] = (abits >> (8 * i)) & 0xFF
    out[:, 8] = c0 & 0xFF
    out[:, 9] = c0 >> 8
    out[:, 10] = c1 & 0xFF
    out[:, 11] = c1 >> 8
    for i in range(4):
        out[:, 12 + i] = (cbits >> (8 * i)) & 0xFF
    return out.tobytes()
