"""numpy BC3 (DXT5) encoder for UI textures.

Retail UI textures are almost all BC3; storing the prototype's A8R8G8B8 textures uncompressed quadruples
their memory footprint (the HUD screen then fails to load), so they are re-encoded here.

Quality matters because HUD art is shown large on high-resolution screens:
  colour: endpoints on the block's principal axis (alpha-weighted), then least-squares refinement of the
          565 endpoints against the chosen indices; the best of all candidates per block is kept.
  alpha:  both BC3 alpha modes are tried per block (8 interpolated levels, or 6 levels + exact 0/255,
          which keeps anti-aliased edges next to fully transparent/opaque texels exact).
"""
import numpy as np

W4 = np.array([[1, 0], [0, 1], [2 / 3, 1 / 3], [1 / 3, 2 / 3]])      # 4-colour palette weights (c0, c1)


def _blocks(img):
    h, w, _ = img.shape
    ph, pw = (-h) % 4, (-w) % 4
    if ph or pw:
        img = np.pad(img, ((0, ph), (0, pw), (0, 0)), mode='edge')
    H, W = img.shape[0] // 4, img.shape[1] // 4
    return img.reshape(H, 4, W, 4, 4).transpose(0, 2, 1, 3, 4).reshape(H * W, 16, 4).astype(np.int32), H, W


def _to565(c):
    c = np.clip(np.rint(c), 0, 255).astype(np.int64)
    return ((c[..., 0] * 31 + 127) // 255 << 11) | ((c[..., 1] * 63 + 127) // 255 << 5) | ((c[..., 2] * 31 + 127) // 255)


def _from565(v):
    r = ((v >> 11) & 31) * 255 // 31
    g = ((v >> 5) & 63) * 255 // 63
    b = (v & 31) * 255 // 31
    return np.stack([r, g, b], -1)


def _palette(c0, c1):
    p0, p1 = _from565(c0), _from565(c1)
    return np.stack([p0, p1, (2 * p0 + p1) // 3, (p0 + 2 * p1) // 3], 1)          # n,4,3


def _fit(rgb, wgt, c0, c1):
    """Indices + weighted squared error for endpoint pair (c0, c1)."""
    pal = _palette(c0, c1)
    d = ((rgb[:, :, None, :] - pal[:, None, :, :]) ** 2).sum(3)                     # n,16,4
    idx = d.argmin(2)
    err = (np.take_along_axis(d, idx[:, :, None], 2)[:, :, 0] * wgt).sum(1)
    return idx, err


def _least_squares(rgb, wgt, idx):
    """Endpoints minimising the weighted error for fixed indices (2x2 normal equations per block)."""
    ab = W4[idx]                                                                     # n,16,2
    aw = ab * wgt[:, :, None]
    m = np.einsum('npi,npj->nij', aw, ab)                                            # n,2,2
    r = np.einsum('npi,npc->nic', aw, rgb)                                           # n,2,3
    det = m[:, 0, 0] * m[:, 1, 1] - m[:, 0, 1] * m[:, 1, 0]
    ok = np.abs(det) > 1e-9
    inv = np.zeros_like(m)
    inv[ok, 0, 0] = m[ok, 1, 1] / det[ok]
    inv[ok, 1, 1] = m[ok, 0, 0] / det[ok]
    inv[ok, 0, 1] = -m[ok, 0, 1] / det[ok]
    inv[ok, 1, 0] = -m[ok, 1, 0] / det[ok]
    e = np.einsum('nij,njc->nic', inv, r)
    return _to565(e[:, 0]), _to565(e[:, 1]), ok


def _encode_colour(rgb, wgt):
    n = len(rgb)
    wsum = wgt.sum(1, keepdims=True)
    mean = (rgb * wgt[:, :, None]).sum(1) / np.maximum(wsum, 1e-9)
    x = rgb - mean[:, None, :]
    cov = np.einsum('npi,npj->nij', x * wgt[:, :, None], x)
    axis = np.ones((n, 3)) / np.sqrt(3)
    for _ in range(8):                                                               # power iteration
        axis = np.einsum('nij,nj->ni', cov, axis)
        axis /= np.maximum(np.linalg.norm(axis, axis=1, keepdims=True), 1e-9)
    t = (x * axis[:, None, :]).sum(2)
    vis = wgt > 0.02
    big = np.where(vis, t, -np.inf).max(1)
    small = np.where(vis, t, np.inf).min(1)
    big = np.where(np.isfinite(big), big, 0)
    small = np.where(np.isfinite(small), small, 0)
    cands = [(_to565(mean + axis * big[:, None]), _to565(mean + axis * small[:, None]))]
    # per-channel bounding box as an extra candidate
    cmax = np.where(vis[:, :, None], rgb, -1).max(1)
    cmin = np.where(vis[:, :, None], rgb, 256).min(1)
    cands.append((_to565(np.clip(cmax, 0, 255)), _to565(np.clip(cmin, 0, 255))))
    best_c0, best_c1 = cands[0]
    best_idx, best_err = _fit(rgb, wgt, best_c0, best_c1)
    for c0, c1 in cands[1:]:
        idx, err = _fit(rgb, wgt, c0, c1)
        better = err < best_err
        best_c0, best_c1 = np.where(better, c0, best_c0), np.where(better, c1, best_c1)
        best_idx, best_err = np.where(better[:, None], idx, best_idx), np.minimum(err, best_err)
    for _ in range(2):                                                               # refinement
        c0, c1, ok = _least_squares(rgb, wgt, best_idx)
        idx, err = _fit(rgb, wgt, c0, c1)
        better = ok & (err < best_err)
        best_c0, best_c1 = np.where(better, c0, best_c0), np.where(better, c1, best_c1)
        best_idx, best_err = np.where(better[:, None], idx, best_idx), np.where(better, err, best_err)
    # BC3 colour blocks are always 4-colour; keep c0 >= c1 anyway (safe for every decoder)
    swap = best_c0 < best_c1
    best_c0, best_c1 = np.where(swap, best_c1, best_c0), np.where(swap, best_c0, best_c1)
    best_idx = np.where(swap[:, None], np.array([1, 0, 3, 2])[best_idx], best_idx)
    return best_c0, best_c1, best_idx


def _alpha_palettes(a0, a1):
    a0 = a0[:, None].astype(np.int64)
    a1 = a1[:, None].astype(np.int64)
    k = np.arange(1, 7)[None, :]
    eight = np.concatenate([a0, a1, ((7 - k) * a0 + k * a1) // 7], 1)                # a0 > a1
    k5 = np.arange(1, 5)[None, :]
    six = np.concatenate([a0, a1, ((5 - k5) * a0 + k5 * a1) // 5,
                          np.zeros_like(a0), np.full_like(a0, 255)], 1)             # a0 <= a1
    return eight, six


def _encode_alpha(a):
    n = len(a)
    hi, lo = a.max(1), a.min(1)
    # 8-level mode needs a0 > a1
    e0 = np.where(hi == lo, np.minimum(hi + 1, 255), hi)
    e1 = np.where((hi == lo) & (hi == 255), lo - 1, lo)
    # 6-level mode: range of the texels that are not exactly 0 or 255
    inner = (a > 0) & (a < 255)
    ihi = np.where(inner, a, -1).max(1)
    ilo = np.where(inner, a, 256).min(1)
    none = ihi < 0
    s0 = np.where(none, 0, ilo)
    s1 = np.where(none, 255, ihi)
    eight, _ = _alpha_palettes(e0, e1)
    _, six = _alpha_palettes(s0, s1)
    d8 = np.abs(a[:, :, None] - eight[:, None, :])
    d6 = np.abs(a[:, :, None] - six[:, None, :])
    i8, i6 = d8.argmin(2), d6.argmin(2)
    err8 = (np.take_along_axis(d8, i8[:, :, None], 2)[:, :, 0] ** 2).sum(1)
    err6 = (np.take_along_axis(d6, i6[:, :, None], 2)[:, :, 0] ** 2).sum(1)
    use6 = err6 < err8
    a0 = np.where(use6, s0, e0)
    a1 = np.where(use6, s1, e1)
    idx = np.where(use6[:, None], i6, i8).astype(np.int64)
    bits = np.zeros(n, np.int64)
    for i in range(16):
        bits |= idx[:, i] << (3 * i)
    return a0, a1, bits


def encode_bc3(rgba):
    """rgba: HxWx4 uint8 -> BC3 bytes (block rows, top to bottom)."""
    blk, H, W = _blocks(rgba)
    n = len(blk)
    a0, a1, abits = _encode_alpha(blk[:, :, 3])
    # colour error is weighted by alpha (invisible texels do not matter)
    wgt = blk[:, :, 3].astype(np.float64) / 255.0 + 1e-3
    c0, c1, cidx = _encode_colour(blk[:, :, :3].astype(np.float64), wgt)
    cbits = np.zeros(n, np.int64)
    for i in range(16):
        cbits |= cidx[:, i].astype(np.int64) << (2 * i)
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
