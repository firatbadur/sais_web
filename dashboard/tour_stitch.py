"""Tesis Sanal Turu — panorama birleştirme motoru (saf fonksiyonlar, DB'siz).

İki yol:

1. **Yönelim projeksiyonu** (`stitch_by_orientation`) — telefon jiroskopundan
   gelen yaw/pitch/roll + görüş açısıyla her kare doğrudan equirectangular
   küreye yansıtılır, kenarlara doğru azalan ağırlıkla (feather) harmanlanır.
   Görüntü eşleştirmesine dayanmadığı için kabin içindeki düz beyaz duvar /
   paslanmaz yüzey gibi özelliksiz sahnelerde de çalışır; geometri tam
   bilindiği için çıktı gerçek 2:1 küredir. Jiroskop kayması küçük dikiş
   kaymaları yaratabilir (amaç "ne nerede"yi göstermek; fotogrametri değil).
2. **OpenCV Stitcher** (`stitch_by_features`) — yönelim yoksa (HTTP / jiroskopsuz
   cihaz). Çıktı küresel (spherical) projeksiyondadır; `embed_partial` ile
   gri dolgulu 2:1 kanvasa yerleştirilir.

Açı sözleşmesi (görüntüleyici Photo Sphere Viewer ile aynı): yaw kuzeyden
saat yönünde (sağa) artar, pitch yukarı pozitif; equirectangular görüntünün
ortası yaw=0. Dünya ekseni: X doğu, Y kuzey, Z yukarı.
"""
from __future__ import annotations

import math

import numpy as np

try:  # opencv-python-headless — Docker imajında var; dev'de eksikse zarif hata
    import cv2
except Exception:  # pragma: no cover - ortam bağımlı
    cv2 = None

FILL_COLOR = (64, 64, 64)  # kapsanmayan bölgeler (BGR)


class StitchError(Exception):
    """Birleştirme yapılamadı — mesaj kullanıcıya gösterilir."""


def _require_cv2():
    if cv2 is None:
        raise StitchError("OpenCV (opencv-python-headless) kurulu değil.")


# --------------------------------------------------------------------------- #
# Görüntü yardımcıları
# --------------------------------------------------------------------------- #

def decode_image(data: bytes):
    _require_cv2()
    arr = np.frombuffer(bytes(data), dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise StitchError("Görüntü çözülemedi (JPEG/PNG değil ya da bozuk).")
    return img


def encode_jpeg(img, quality: int = 85) -> bytes:
    _require_cv2()
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise StitchError("JPEG kodlanamadı.")
    return buf.tobytes()


def limit_size(img, max_px: int):
    """Uzun kenarı `max_px`'i aşıyorsa orantılı küçült."""
    h, w = img.shape[:2]
    longest = max(h, w)
    if max_px and longest > max_px:
        s = max_px / float(longest)
        img = cv2.resize(img, (max(1, int(w * s)), max(1, int(h * s))),
                         interpolation=cv2.INTER_AREA)
    return img


def normalize_jpeg(data: bytes, max_px: int, quality: int = 85):
    """Gelen görüntüyü çöz → boyut sınırla → JPEG. (bytes, w, h) döndürür."""
    img = limit_size(decode_image(data), max_px)
    h, w = img.shape[:2]
    return encode_jpeg(img, quality), w, h


def make_thumbnail(pano, width: int = 480) -> bytes:
    h, w = pano.shape[:2]
    s = width / float(w)
    small = cv2.resize(pano, (width, max(1, int(h * s))), interpolation=cv2.INTER_AREA)
    return encode_jpeg(small, 80)


# --------------------------------------------------------------------------- #
# Geometri
# --------------------------------------------------------------------------- #

def camera_basis(yaw_deg: float, pitch_deg: float, roll_deg: float):
    """Kamera (sağ, yukarı, ileri) birim vektörleri — dünya ekseninde.

    Roll sözleşmesi (mobil çekim JS'i ile aynı): u = cosφ·up0 − sinφ·right0.
    """
    psi, th, phi = (math.radians(yaw_deg), math.radians(pitch_deg),
                    math.radians(roll_deg))
    fwd = np.array([math.sin(psi) * math.cos(th), math.cos(psi) * math.cos(th),
                    math.sin(th)])
    right0 = np.array([math.cos(psi), -math.sin(psi), 0.0])
    up0 = np.cross(right0, fwd)
    up = math.cos(phi) * up0 - math.sin(phi) * right0
    right = np.cross(fwd, up)
    return right, up, fwd


def direction_to_yaw_pitch(vec) -> tuple[float, float]:
    x, y, z = (float(v) for v in vec)
    n = math.sqrt(x * x + y * y + z * z) or 1.0
    return math.degrees(math.atan2(x, y)), math.degrees(math.asin(max(-1.0, min(1.0, z / n))))


def frame_point_to_yaw_pitch(nx: float, ny: float, yaw: float, pitch: float, roll: float,
                             hfov: float, aspect: float) -> tuple[float, float]:
    """Kare üzerindeki normalize nokta (0..1, sol-üst orijin) → küre yaw/pitch.

    `aspect` = genişlik/yükseklik. AI bbox merkezlerini panoramaya taşımak için.
    """
    right, up, fwd = camera_basis(yaw, pitch, roll)
    t = math.tan(math.radians(hfov) / 2.0)
    x = (nx * 2.0 - 1.0) * t
    y = (1.0 - ny * 2.0) * t / aspect
    return direction_to_yaw_pitch(fwd + x * right + y * up)


# --------------------------------------------------------------------------- #
# Yol 1 — yönelim projeksiyonu
# --------------------------------------------------------------------------- #

def stitch_by_orientation(frames, out_width: int = 4096):
    """`frames`: [{"img": ndarray BGR, "yaw", "pitch", "roll", "hfov"}, ...] → equirect BGR."""
    _require_cv2()
    if not frames:
        raise StitchError("Birleştirilecek kare yok.")
    W = int(out_width) - int(out_width) % 2
    H = W // 2
    acc = np.zeros((H, W, 3), dtype=np.float32)
    wsum = np.zeros((H, W), dtype=np.float32)

    lon_all = (np.arange(W, dtype=np.float32) + 0.5) / W * (2 * np.pi) - np.pi
    lat_all = np.pi / 2 - (np.arange(H, dtype=np.float32) + 0.5) / H * np.pi

    for fr in frames:
        img = fr["img"]
        ih, iw = img.shape[:2]
        hfov = float(fr.get("hfov") or 0) or 60.0
        f = (iw / 2.0) / math.tan(math.radians(hfov) / 2.0)
        half_diag = math.degrees(math.atan(math.hypot(iw, ih) / 2.0 / f)) + 2.0
        right, up, fwd = camera_basis(fr["yaw"], fr["pitch"], fr["roll"])

        # Satır bandı: karenin kapsayabileceği enlem aralığı (hız için).
        lat_hi = min(90.0, fr["pitch"] + half_diag)
        lat_lo = max(-90.0, fr["pitch"] - half_diag)
        r0 = max(0, int((90.0 - lat_hi) / 180.0 * H) - 1)
        r1 = min(H, int((90.0 - lat_lo) / 180.0 * H) + 2)
        if r1 <= r0:
            continue
        lat = lat_all[r0:r1][:, None]
        cl = np.cos(lat)
        dx = np.sin(lon_all)[None, :] * cl
        dy = np.cos(lon_all)[None, :] * cl
        dz = np.broadcast_to(np.sin(lat), dx.shape)

        cx = dx * right[0] + dy * right[1] + dz * right[2]
        cy = dx * up[0] + dy * up[1] + dz * up[2]
        cz = dx * fwd[0] + dy * fwd[1] + dz * fwd[2]
        front = cz > 1e-3
        cz_safe = np.where(front, cz, 1.0)
        mx = (iw / 2.0 + f * cx / cz_safe).astype(np.float32)
        my = (ih / 2.0 - f * cy / cz_safe).astype(np.float32)
        inside = front & (mx >= 0) & (mx <= iw - 1) & (my >= 0) & (my <= ih - 1)
        if not inside.any():
            continue

        sampled = cv2.remap(img, mx, my, interpolation=cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        # Feather: kare merkezinde 1, kenarda ~0 → dikişler yumuşar.
        nx = np.abs(mx / (iw - 1) * 2 - 1)
        ny = np.abs(my / (ih - 1) * 2 - 1)
        w = np.clip((1 - nx), 0, 1) * np.clip((1 - ny), 0, 1)
        w = np.where(inside, w * w + 1e-4, 0).astype(np.float32)
        acc[r0:r1] += sampled.astype(np.float32) * w[..., None]
        wsum[r0:r1] += w

    covered = wsum > 0
    if not covered.any():
        raise StitchError("Kareler küreye yansıtılamadı (yönelim verisi geçersiz).")
    out = np.empty((H, W, 3), dtype=np.uint8)
    out[:] = FILL_COLOR
    out[covered] = np.clip(acc[covered] / wsum[covered][:, None], 0, 255).astype(np.uint8)
    return _soften_gaps(out, covered)


def _soften_gaps(pano, covered):
    """Kapsanmayan (zenit/nadir) bölgeleri komşu renklerin bulanığıyla doldur."""
    if covered.all():
        return pano
    H, W = pano.shape[:2]
    small_w = max(64, W // 16)
    small_h = max(32, H // 16)
    s_img = cv2.resize(pano, (small_w, small_h), interpolation=cv2.INTER_AREA)
    s_mask = cv2.resize((~covered).astype(np.uint8) * 255, (small_w, small_h),
                        interpolation=cv2.INTER_NEAREST)
    filled = cv2.inpaint(s_img, s_mask, 5, cv2.INPAINT_TELEA)
    filled = cv2.GaussianBlur(filled, (0, 0), 3)
    big = cv2.resize(filled, (W, H), interpolation=cv2.INTER_LINEAR)
    out = pano.copy()
    out[~covered] = big[~covered]
    return out


# --------------------------------------------------------------------------- #
# Yol 2 — özellik eşleştirmeli OpenCV Stitcher (yönelimsiz kareler)
# --------------------------------------------------------------------------- #

def stitch_by_features(images, hfov: float = 60.0, out_width: int = 4096):
    _require_cv2()
    if len(images) < 2:
        raise StitchError("Yönelimsiz birleştirme için en az 2 kare gerekir.")
    stitcher = cv2.Stitcher_create(cv2.Stitcher_PANORAMA)
    status, pano = stitcher.stitch(list(images))
    if status != cv2.Stitcher_OK or pano is None:
        reasons = {
            1: "Karelerde yeterli ortak nokta bulunamadı — kareler arasında daha fazla örtüşme bırakın.",
            2: "Kamera konumu tahmin edilemedi.",
            3: "Kamera parametreleri ayarlanamadı.",
        }
        raise StitchError(reasons.get(status, "Görüntüler birleştirilemedi (kod %s)." % status))
    # Küresel warper: piksel/radyan ≈ kare odak uzaklığı (her iki eksende aynı).
    iw = images[0].shape[1]
    focal = (iw / 2.0) / math.tan(math.radians(hfov) / 2.0)
    return embed_partial(pano, focal=focal, out_width=out_width)


def embed_partial(img, *, focal: float | None = None, hspan_deg: float | None = None,
                  out_width: int = 4096):
    """Kısmi (360° olmayan) panoramayı gri dolgulu 2:1 equirect kanvasa ortala.

    Ya `focal` (piksel/radyan) ya da `hspan_deg` (yatay kapsama açısı) verilir.
    Kapsama ~360°'ye yakınsa tam tur kabul edilir.
    """
    _require_cv2()
    h, w = img.shape[:2]
    if focal:
        hspan = math.degrees(w / focal)
    else:
        hspan = float(hspan_deg or 360.0)
    if hspan >= 330:
        hspan = 360.0
    hspan = max(10.0, min(360.0, hspan))
    full_w = w * 360.0 / hspan
    scale = min(1.0, out_width / full_w)
    W = int(round(full_w * scale)) // 2 * 2
    W = max(W, 2)
    H = W // 2
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    if nh > H:  # dikey kapsama 180°'yi aşamaz
        k = H / float(nh)
        nw, nh = max(1, int(nw * k)), H
    resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
    out = np.empty((H, W, 3), dtype=np.uint8)
    out[:] = FILL_COLOR
    x0 = (W - nw) // 2
    y0 = (H - nh) // 2
    out[y0:y0 + nh, x0:x0 + nw] = resized
    covered = np.zeros((H, W), dtype=bool)
    covered[y0:y0 + nh, x0:x0 + nw] = True
    return out if covered.all() else _soften_gaps(out, covered)


def prepare_uploaded_panorama(data: bytes, hspan_deg: float | None, out_width: int):
    """Telefonun kendi panorama/Photo Sphere çıktısı → 2:1 equirect.

    Oran ~2:1 ise doğrudan küre kabul edilir; değilse (yatay şerit panorama)
    `hspan_deg` (varsayılan 360) ile kısmi yerleştirilir.
    """
    img = decode_image(data)
    h, w = img.shape[:2]
    ratio = w / float(h)
    if 1.9 <= ratio <= 2.1 and not hspan_deg:
        if w > out_width:
            img = cv2.resize(img, (out_width, out_width // 2), interpolation=cv2.INTER_AREA)
        elif abs(ratio - 2.0) > 1e-3:
            img = cv2.resize(img, (w, w // 2), interpolation=cv2.INTER_AREA)
        return img
    return embed_partial(img, hspan_deg=hspan_deg or 360.0, out_width=out_width)
