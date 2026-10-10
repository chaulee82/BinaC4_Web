"""
Module: core/volume_projection.py
Dự án: BinaC4
Mục đích: Nội suy Khối lượng theo Tỷ trọng thời gian (Time-Weighted Volume Extrapolation) — tiện ích dùng chung.

Vấn đề: So Volume của nến ĐANG CHẠY với MA(Volume) của các nến ĐÃ ĐÓNG → luôn đánh giá thấp dòng tiền
        ở đầu nến (nến 4H mới chạy 1 tiếng chỉ có ~25% thời gian để tích lũy Volume).

Công thức:
    Tỷ trọng       = (now − open_time) / thời lượng 1 nến
    Volume Dự Phóng = Volume hiện tại / Tỷ trọng   (hệ số nhân = 1 / Tỷ trọng)

Chốt chặn (Guardrails):
    • Chỉ áp dụng cho khung LỚN (4H, 1D, 3D). Khung nhỏ (15M, 1H) dùng Volume thực — 1 lệnh Market Buy
      ở phút đầu nến 15M sẽ bị nhân ×16 → tín hiệu bạo phát ảo (Fakeout).
    • Tỷ trọng tối thiểu theo khung: 4H ≥ 20% (~48 phút), 1D / 3D ≥ 10%. Dưới ngưỡng → giữ Volume thực.
    • Trần hệ số nhân 5.0x.
"""

import time

TIMEFRAME_MS = {
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
    "3d": 3 * 24 * 60 * 60 * 1000,
}

# Khung được phép nội suy → tỷ trọng tối thiểu. Khung không có trong bảng = KHÔNG nội suy.
PROJECTION_MIN_FRAC = {
    "4h": 0.20,
    "1d": 0.10,
    "3d": 0.10,
}
PROJECTION_MAX_MULT = 5.0


def volume_time_weight(open_time_ms, candle_ms: int, now_ms: int = None,
                       min_frac: float = 0.10, max_mult: float = PROJECTION_MAX_MULT) -> dict:
    """
    Tỷ trọng thời gian của nến đang chạy + hệ số nội suy (đã qua chốt chặn).
      frac < min_frac → mult = 1.0 (giữ Vol thực, applied=False)
      frac ≥ 100%     → mult = 1.0 (nến đã đủ thời gian)
      còn lại         → mult = min(1/frac, max_mult)
    Thiếu open_time → không nội suy (fail-safe).
    """
    try:
        if open_time_ms is None or open_time_ms != open_time_ms:  # None / NaN
            return {"frac": None, "mult": 1.0, "applied": False}
        now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
        frac = (now_ms - int(float(open_time_ms))) / float(candle_ms)
        frac = max(0.0, min(frac, 1.0))
        if frac < min_frac or frac >= 1.0:
            return {"frac": frac, "mult": 1.0, "applied": False}
        return {"frac": frac, "mult": min(1.0 / frac, max_mult), "applied": True}
    except Exception:
        return {"frac": None, "mult": 1.0, "applied": False}


def project_volume(volume: float, open_time_ms, timeframe: str, now_ms: int = None) -> dict:
    """
    Volume Dự Phóng cho nến đang chạy theo khung `timeframe`.
    Khung nhỏ / không hỗ trợ → trả Volume thực (eligible=False).
    Trả về: {volume_proj, frac, mult, applied, eligible}
    """
    tf = (timeframe or "").lower()
    if tf not in PROJECTION_MIN_FRAC or tf not in TIMEFRAME_MS:
        return {"volume_proj": float(volume), "frac": None, "mult": 1.0, "applied": False, "eligible": False}
    tw = volume_time_weight(open_time_ms, TIMEFRAME_MS[tf], now_ms, min_frac=PROJECTION_MIN_FRAC[tf])
    return {"volume_proj": float(volume) * tw["mult"], **tw, "eligible": True}
