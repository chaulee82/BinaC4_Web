"""
Module: views/summary_board.py
Dự án: BinaC4
Mục đích: BẢNG TỔNG KẾT TỐI ƯU (SUMMARY_BOARD) — chạy sau "Hoàn tất quét thị trường".

Gom tín hiệu từ 5 Động cơ (global_signals_pool do ConsoleRenderer thu thập khi in bảng)
rồi lọc / xếp hạng thành 3 bảng hành động:
    1. 🎯 SPOT (Lướt sóng OCO)       — DC2/DC3/DC4 APPROVED, SL ≤ 6%, R/R↓ → Money Flow↓, Top 2
    2. 🥅 GRID TP2 (Đón nhúng)        — dòng `🎯 [GRID TP2 -`, không NÉ GRID, Score↓, Top 2
    3. 🦅 WIDE GRID 3D (Lưới vĩ mô)   — dòng `🦅 [WIDE GRID 3D -`, Vola24h ≥ 5%, 3D Chân sóng / Cột cờ,
                                        Bounces↓ → Score↓, Top 2

Cơ chế MÃ DỰ PHÒNG (Fallback): mỗi bảng chia 2 danh sách song song
    primary_list (chuẩn cứng) | backup_list (chuẩn mềm).
    primary rỗng → lấy Top 2 từ backup, gắn thẻ [⚠️ DỰ PHÒNG]. Cả 2 rỗng → báo không có setup.
    Chốt chặn sinh tử (CẤP 3 KHẨN CẤP, GÃY MA7 3D) và Công tắc BTC vẫn áp dụng cho cả mã dự phòng.

Thiết kế: Pure logic (không gọi API) — mọi dữ liệu ngữ cảnh truyền vào qua tham số. Fail-safe.
"""

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional

# ── Hằng số đặc tả ───────────────────────────────────────────────────────────
SPOT_ENGINES          = ("DC2", "DC3", "DC4")
STATUS_APPROVED       = "APPROVED 🟢"
STATUS_PULLBACK       = "🎯 🚀 VÀO LỆNH PULLBACK"
STATUS_REJECTED       = "🔴 TỪ CHỐI"
STATUS_WAIT           = "⏳ CHỜ"
SPOT_ALLOWED_STATUS   = (STATUS_APPROVED, STATUS_PULLBACK)
SPOT_MAX_SL_PCT       = -6.0          # SL_Percent < -6.0% → Loại (chuẩn cứng)
SPOT_BACKUP_MAX_SL    = -10.0         # Nới lỏng cho mã dự phòng
DC2_BACKUP_MIN_SCORE  = 70            # DC2 dính EW CẤP 1 vẫn phải có điểm Pullback cao (ngưỡng lệnh Sniper)
WIDE_GRID_MIN_VOLA    = 5.0
BTC_KILL_SWITCH_TEXT  = "Fakeout Risk Cao"
TOP_N                 = 2

TAG_GRID_TP2          = "🎯 [GRID TP2 -"
TAG_WIDE_GRID         = "🦅 [WIDE GRID 3D -"
TAG_SETUP             = "⚙️ SETUP"
WIDE_GRID_3D_SIGNALS  = ("🚀 [3D: CHÂN SÓNG", "CỘT CỜ CAO")
BACKUP_TAG            = "[⚠️ DỰ PHÒNG]"
TAG_STRICT_OK         = "🛡️ [3D STRICT ✅]"
TAG_STRICT_FAIL       = "⛔ [3D STRICT]"
GRID_KINDS            = ("GRID_TP2", "WIDE_GRID_3D")

# Chốt chặn sinh tử — áp dụng cho CẢ primary lẫn backup
FATAL_EW_LEVEL        = 3                                    # 💀 CẤP 3: KHẨN CẤP
FATAL_TEXTS           = ("GÃY MA7 3D", "GÃY ĐƯỜNG RAY MA7 3D")

# Phân tách loại cấu hình Wide Grid theo Động cơ nguồn
WIDE_GRID_TAGS        = {"DC5": "Nén Vol Chờ Nổ", "DC1": "Chân Sóng Vĩ Mô",
                         "DC2": "Pullback Sniper", "DC4": "Hot Trend"}
# Ưu tiên khi đồng hạng Bounces: DC5 > DC1 > các động cơ khác
WIDE_GRID_ENGINE_PRIO = {"DC5": 3, "DC1": 2}

_RE_MONEY_FLOW = re.compile(r"TIỀN BẠO PHÁT \((\d+(?:\.\d+)?)x\)")
_RE_FLOW_TAG   = re.compile(r"x(\d+(?:\.\d+)?)")
_RE_RR         = re.compile(r"R/R=1:(\d+(?:\.\d+)?)")
_RE_SL_PCT     = re.compile(r"SL=[^|(]*\((-?\d+(?:\.\d+)?)%\)")
_RE_TRIG       = re.compile(r"Trig:\s*([\d.]+)")
_RE_BOUNCES    = re.compile(r"B:(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)%")


@dataclass
class SignalRecord:
    """1 tín hiệu (1 mã × 1 Động cơ) trong global_signals_pool."""
    symbol: str                       # Mã sạch (VD: 'RESOLV')
    engine_source: str                # DC1..DC5
    action_status: str = STATUS_WAIT  # APPROVED 🟢 | 🎯 🚀 VÀO LỆNH PULLBACK | 🔴 TỪ CHỐI | ⏳ CHỜ
    action_label: str = ""            # Nhãn hành động gốc của Động cơ
    target_score: float = 0.0
    money_flow: float = 0.0           # Hệ số tiền bạo phát (VD 3.6)
    rr_ratio: float = 0.0
    sl_percent: Optional[float] = None  # Âm (VD -4.0)
    macro_3d_signal: str = ""         # VD '🚀 [3D: CHÂN SÓNG BỨT PHÁ - SIÊU SÓNG]'
    vola_24h: float = 0.0             # Enrich từ live_data_map
    grid_warning: str = ""            # Enrich từ coin_filter (Phân Loại Grid)
    rebalance_score: float = 0.0      # Enrich từ coin_filter (cột TỔNG — Bảng Rebalance / Spot Grid)
    ew_level: int = 0                 # Enrich từ Early Warning Matrix (3 = KHẨN CẤP)
    setup_line: str = ""              # Chuỗi gốc `⚙️ SETUP...`
    grid_tp2_line: str = ""           # Chuỗi gốc `🎯 [GRID TP2 - ...`
    wide_grid_line: str = ""          # Chuỗi gốc `🦅 [WIDE GRID 3D - ...`
    bounces: float = 0.0              # DC5: số lần nảy (B:{bounces}x{range}%)
    bounce_range: float = 0.0         # DC5: biên độ nảy %

    @property
    def raw_setup_string(self) -> str:
        return " || ".join(s for s in (self.setup_line, self.grid_tp2_line, self.wide_grid_line) if s)


# ── Helpers trích xuất (dùng chung cho Collector) ────────────────────────────
def clean_line(line: Optional[str]) -> str:
    """Bỏ thụt lề & mũi tên '↳' để chuỗi bắt đầu đúng từ emoji gốc."""
    if not line:
        return ""
    s = line.strip()
    if s.startswith("↳"):
        s = s[1:].strip()
    return s


def extract_money_flow(*texts: Any) -> float:
    """Lấy hệ số lớn nhất từ `💥 TIỀN BẠO PHÁT (3.6x)` hoặc tag dòng tiền `💰x3.4`."""
    best = 0.0
    for t in texts:
        if not t:
            continue
        t = str(t)
        for m in _RE_MONEY_FLOW.findall(t):
            best = max(best, float(m))
        if "💰" in t:
            for m in _RE_FLOW_TAG.findall(t):
                best = max(best, float(m))
    return best


def extract_rr(text: str) -> float:
    m = _RE_RR.search(text or "")
    return float(m.group(1)) if m else 0.0


def extract_sl_percent(text: str) -> Optional[float]:
    m = _RE_SL_PCT.search(text or "")
    return float(m.group(1)) if m else None


def extract_bounces(text: Any) -> tuple:
    m = _RE_BOUNCES.search(str(text or ""))
    return (float(m.group(1)), float(m.group(2))) if m else (0.0, 0.0)


def classify_action(engine: str, action_label: str, extra_texts: Iterable[Any] = ()) -> str:
    """Chuẩn hóa nhãn hành động của từng Động cơ về action_status của Summary Board."""
    act = action_label or ""
    blob = " ".join(str(x) for x in (act, *extra_texts) if x is not None)
    if "TỪ CHỐI" in blob or "HỦY SETUP" in blob or "GÃY MA7 3D" in act:
        return STATUS_REJECTED
    if engine == "DC4" and "VÀO LỆNH PULLBACK" in act:
        return STATUS_PULLBACK
    if engine in ("DC2", "DC3") and act.strip().startswith("🟢"):
        return STATUS_APPROVED
    return STATUS_WAIT


# ── Enrich ngữ cảnh ──────────────────────────────────────────────────────────
def _dedupe_keep_first(signals: List[SignalRecord]) -> List[SignalRecord]:
    seen, out = set(), []
    for s in signals:
        if s.symbol not in seen:
            seen.add(s.symbol)
            out.append(s)
    return out


def _build_summary_maps(df_summary) -> tuple:
    """(grid_warning_map, rebalance_score_map) từ df_summary của coin_filter."""
    warn: Dict[str, str] = {}
    score: Dict[str, float] = {}
    try:
        if df_summary is not None and not df_summary.empty:
            for r in df_summary.to_dict(orient="records"):
                sym = str(r.get("Symbol", "")).upper()
                if not sym:
                    continue
                warn[sym] = str(r.get("Phân Loại Grid", "") or "")
                try:
                    score[sym] = float(r.get("TỔNG", 0) or 0)
                except (TypeError, ValueError):
                    score[sym] = 0.0
    except Exception:
        pass
    return warn, score


def _build_ew_level_map(warning_results) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in warning_results or []:
        sym = str(r.get("symbol", "")).upper().replace("/", "")
        if sym.endswith("USDT") and len(sym) > 4:
            sym = sym[:-4]
        lvl = int(r.get("level", 0) or 0)
        if "KHẨN CẤP" in str(r.get("label", "")):
            lvl = max(lvl, FATAL_EW_LEVEL)
        out[sym] = lvl
    return out


def _enrich(pool: List[SignalRecord], live_data_map: Optional[Dict[str, Any]],
            df_summary, warning_results) -> None:
    live_data_map = live_data_map or {}
    warn_map, rebal_map = _build_summary_maps(df_summary)
    ew_map = _build_ew_level_map(warning_results)
    for s in pool:
        info = live_data_map.get(f"{s.symbol}USDT") or {}
        try:
            s.vola_24h = float(info.get("daily_vola", 0.0) or 0.0)
        except (TypeError, ValueError):
            s.vola_24h = 0.0
        s.grid_warning = warn_map.get(s.symbol, "")
        s.rebalance_score = rebal_map.get(s.symbol, 0.0)
        s.ew_level = ew_map.get(s.symbol, 0)


# ── Phân loại primary / backup ───────────────────────────────────────────────
def is_fatal_error(s: SignalRecord) -> bool:
    """Chốt chặn sinh tử: CẤP 3 KHẨN CẤP hoặc GÃY MA7 3D → loại khỏi MỌI bảng (kể cả dự phòng)."""
    if s.ew_level >= FATAL_EW_LEVEL:
        return True
    blob = f"{s.action_label} {s.macro_3d_signal}"
    return any(t in blob for t in FATAL_TEXTS)


def _spot_sl(s: SignalRecord) -> Optional[float]:
    return s.sl_percent if s.sl_percent is not None else extract_sl_percent(s.setup_line)


def _is_spot_primary(s: SignalRecord) -> bool:
    sl = _spot_sl(s)
    return (s.engine_source in SPOT_ENGINES and TAG_SETUP in s.setup_line
            and s.action_status in SPOT_ALLOWED_STATUS
            and sl is not None and sl >= SPOT_MAX_SL_PCT)


def _is_spot_backup(s: SignalRecord) -> bool:
    """Chuẩn mềm: SL ≤ 10%, không bị TỪ CHỐI, và thuộc 1 trong các nhóm:
       - Đã duyệt (APPROVED / VÀO LỆNH) nhưng SL sâu 6–10%
       - DC4 `⏳ CHỜ XÁC NHẬN`
       - DC2 điểm Pullback cao (≥ 70) nhưng tạm dính `⚠️ EW CẤP 1` (chưa bứt hẳn lên MA7)"""
    sl = _spot_sl(s)
    if s.engine_source not in SPOT_ENGINES or TAG_SETUP not in s.setup_line:
        return False
    if s.action_status == STATUS_REJECTED or sl is None or sl < SPOT_BACKUP_MAX_SL:
        return False
    if s.action_status in SPOT_ALLOWED_STATUS:
        return True
    if s.engine_source == "DC4" and "CHỜ XÁC NHẬN" in s.action_label:
        return True
    if s.engine_source == "DC2" and s.ew_level == 1 and s.target_score >= DC2_BACKUP_MIN_SCORE:
        return True
    return False


def _has_grid_tp2(s: SignalRecord) -> bool:
    return TAG_GRID_TP2 in s.grid_tp2_line and bool(_RE_TRIG.search(s.grid_tp2_line))


def _is_wide_primary(s: SignalRecord) -> bool:
    return (s.vola_24h >= WIDE_GRID_MIN_VOLA
            and any(sig in s.macro_3d_signal for sig in WIDE_GRID_3D_SIGNALS))


def classify_pool(pool: List[SignalRecord], spot_enabled: bool = True) -> Dict[str, List[SignalRecord]]:
    """Vòng lặp phân loại duy nhất → 6 danh sách primary / backup."""
    b = {k: [] for k in ("spot_primary", "spot_backup", "tp2_primary", "tp2_backup",
                         "wide_primary", "wide_backup")}
    for s in pool:
        if is_fatal_error(s):
            continue
        # 1. Grid TP2 — lưới trượt cổng Strict 3D (⛔) bị loại hẳn, kể cả dự phòng
        if _has_grid_tp2(s) and TAG_STRICT_FAIL not in s.grid_tp2_line:
            (b["tp2_backup"] if "NÉ GRID" in s.grid_warning else b["tp2_primary"]).append(s)
        # 2. Spot
        if spot_enabled:
            if _is_spot_primary(s):
                b["spot_primary"].append(s)
            elif _is_spot_backup(s):
                b["spot_backup"].append(s)
        # 3. Wide Grid 3D — lưới trượt cổng Strict 3D (⛔) bị loại hẳn
        if TAG_WIDE_GRID in s.wide_grid_line and TAG_STRICT_FAIL not in s.wide_grid_line:
            (b["wide_primary"] if _is_wide_primary(s) else b["wide_backup"]).append(s)
    return b


# ── Hàm sắp xếp ──────────────────────────────────────────────────────────────
def sort_spot_primary(lst):
    """Spot: ưu tiên 1 = 💥 Tiền Bạo Phát, ưu tiên 2 = R:R (cả 2 đều chuẩn hóa, so được giữa các Động cơ)."""
    return sorted(lst, key=lambda s: (_num(s.money_flow), _num(s.rr_ratio)), reverse=True)


def sort_spot_backup(lst):
    return sorted(lst, key=lambda s: (_num(s.money_flow), _num(s.rr_ratio), _num(s.target_score)), reverse=True)
def sort_by_score(lst):      return sorted(lst, key=lambda s: s.target_score, reverse=True)


def sort_grid_tp2(lst):
    """Bảng 2: thang điểm DC2 / DC3 / DC4 khác hệ quy chiếu (DC4 dễ 95–115đ, DC3 ~60–85đ)
    → ưu tiên 1 = 💥 Tiền Bạo Phát (đã chuẩn hóa, cùng ý nghĩa mọi Động cơ), ưu tiên 2 = Điểm."""
    return sorted(lst, key=lambda s: (_num(s.money_flow), _num(s.target_score)), reverse=True)


def sort_wide_primary(lst):
    return sorted(lst, key=lambda s: (s.bounces, s.bounce_range,
                                      WIDE_GRID_ENGINE_PRIO.get(s.engine_source, 1), s.target_score),
                  reverse=True)


def sort_wide_backup(lst):
    # Điểm Rebalance (cột TỔNG) cao nhất → ưu tiên tín hiệu DC1 → điểm Động cơ
    return sorted(lst, key=lambda s: (s.rebalance_score, s.engine_source == "DC1", s.target_score),
                  reverse=True)


def pick_with_fallback(primary: List[SignalRecord], backup: List[SignalRecord],
                       sort_primary: Callable, sort_backup: Callable,
                       blacklist: Iterable[str] = (), top_n: int = TOP_N,
                       fill_with_backup: bool = True) -> tuple:
    """Cơ chế LẤP ĐẦY (Fill-up):
        1. Lấy tối đa top_n mã từ primary.
        2. Nếu chỉ có X < top_n → lấy thêm (top_n − X) mã từ backup (bỏ mã đã chọn).
       fill_with_backup=False → hành vi cũ: chỉ dùng backup khi primary rỗng.
    Trả về (danh sách cuối, set mã dự phòng)."""
    top_n = max(0, int(top_n))
    bl = set(blacklist)
    picked = _dedupe_keep_first(sort_primary([s for s in primary if s.symbol not in bl]))[:top_n]
    if picked and not fill_with_backup:
        return picked, set()
    slots = top_n - len(picked)
    if slots <= 0:
        return picked, set()
    taken = bl | {s.symbol for s in picked}
    extra = _dedupe_keep_first(sort_backup([s for s in backup if s.symbol not in taken]))[:slots]
    return picked + extra, {s.symbol for s in extra}


# ── Render ───────────────────────────────────────────────────────────────────
def _fmt_score(v: float) -> str:
    return f"{v:.0f}" if float(v).is_integer() else f"{v:.2f}"


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


# Dòng setup gốc tương ứng từng bảng (raw_setup_string gộp cả 3 → không dùng để in)
_LINE_BY_KIND = {"SPOT": "setup_line", "GRID_TP2": "grid_tp2_line", "WIDE_GRID_3D": "wide_grid_line"}


def print_formatted_table(signals_list: List[SignalRecord], kind: str, backup_symbols: Any = False) -> None:
    """In bảng theo cơ chế Lắp ráp mảng động: 4 thông số cốt lõi (Điểm, 💥 Tiền Bạo Phát, R:R, Sóng 3D)
    đồng nhất cho mọi cách đánh — trường nào thiếu dữ liệu tự ẩn, không thừa dấu `|`.
    backup_symbols: set mã dự phòng (gắn [⚠️ DỰ PHÒNG] từng dòng) — hoặc bool (True = cả bảng)."""
    if not signals_list:
        print("   Không có setup nào đạt chuẩn (kể cả dự phòng).")
        return
    if isinstance(backup_symbols, bool):
        backup_symbols = {s.symbol for s in signals_list} if backup_symbols else set()
    backup_symbols = set(backup_symbols or ())
    n_backup = sum(1 for s in signals_list if s.symbol in backup_symbols)
    if n_backup and n_backup == len(signals_list):
        print("   💡 Không có mã đạt chuẩn vàng → hiển thị MÃ DỰ PHÒNG (hạng 2 — cân nhắc giảm khối lượng vốn)")
    elif n_backup:
        print(f"   💡 Bổ sung {n_backup} MÃ DỰ PHÒNG cho đủ slot (hạng 2 — chỉ đánh thăm dò / Watchlist)")

    for i, signal in enumerate(signals_list, 1):
        is_backup = signal.symbol in backup_symbols
        prefix = f"{BACKUP_TAG} " if is_backup else ""
        engine = getattr(signal, "engine_source", "") or ""
        score = _num(getattr(signal, "target_score", 0))
        mf = _num(getattr(signal, "money_flow", 0))
        rr = _num(getattr(signal, "rr_ratio", 0))
        vola = _num(getattr(signal, "vola_24h", 0))
        bounces = _num(getattr(signal, "bounces", 0))
        b_range = _num(getattr(signal, "bounce_range", 0))
        macro_3d = getattr(signal, "macro_3d_signal", "") or ""

        components: List[str] = []
        if engine:
            components.append(engine)
        if score:
            components.append(f"{_fmt_score(score)}đ")
        if kind == "WIDE_GRID_3D":
            if bounces:                                             # DC5 PingPong
                components.append(f"Bounces {bounces:g}x{b_range:g}%")
            if vola:
                components.append(f"Vola24H {vola:.1f}%")
            components.append(f"🏷️ {WIDE_GRID_TAGS.get(engine, engine)}")
        if mf > 0:                                                  # DC2/DC3/DC4
            components.append(f"💥 {mf:.1f}x")
        if rr > 0:                                                  # DC2/DC3/DC4
            components.append(f"R:R=1:{rr:.1f}")
        # Lý do hạng 2 (chỉ hiện ở mã dự phòng)
        if is_backup:
            if kind == "SPOT" and getattr(signal, "ew_level", 0) == 1:
                components.append("⚠️ EW CẤP 1")
            if kind == "SPOT" and getattr(signal, "action_status", "") == STATUS_WAIT:
                components.append(signal.action_label.strip()[:40])
            if kind == "GRID_TP2" and "NÉ GRID" in (signal.grid_warning or ""):
                components.append(signal.grid_warning)
            if kind == "WIDE_GRID_3D" and getattr(signal, "rebalance_score", 0):
                components.append(f"Rebalance {signal.rebalance_score:.1f}")
        if macro_3d:
            components.append(macro_3d)

        setup_str = getattr(signal, _LINE_BY_KIND.get(kind, ""), "") or ""
        # 🛡️ Tag kiểm dịch Strict 3D — đưa lên dòng tiêu đề, bỏ khỏi dòng setup để không lặp
        if kind in GRID_KINDS and TAG_STRICT_OK in setup_str:
            components.insert(1 if engine else 0, TAG_STRICT_OK)
            setup_str = setup_str.replace(f" | {TAG_STRICT_OK}", "").replace(TAG_STRICT_OK, "").rstrip(" |")

        print(f"  #{i} {prefix}{signal.symbol:<8} | " + " | ".join(c for c in components if c))
        if setup_str:
            print(f"     ↳ {setup_str}")


_RE_PAREN = re.compile(r"\s*\([^)]*\)")
_STRICT_LAYER_NAMES = {1: "Supertrend 3D", 2: "MA xếp tầng", 3: "Kiệt cung Vol", 4: "Bollinger 3D"}


def _strict_group_key(reason: str) -> str:
    """'Vi phạm Tầng 3 - Vol chưa cạn (0.88x thân cờ)' → 'Tầng 3 - Vol chưa cạn' (bỏ số liệu để gom nhóm)."""
    return _RE_PAREN.sub("", reason or "").replace("Vi phạm ", "").strip() or "Không rõ"


def print_strict_3d_rejections(rejections: Optional[Dict[str, Dict[str, Any]]]) -> None:
    """Mục thống kê (dạng gọn): gom mã bị loại theo LÝ DO CHÍNH (tầng thấp nhất bị vi phạm), mỗi nhóm 1 dòng.
    Ký hiệu `MÃ×N` = trượt N tầng. Nhóm 🟡 Sát chuẩn = chỉ trượt đúng 1 tầng (đáng theo dõi).
    rejections: {sym: {"engines": set, "kinds": set, "reasons": [...], "failed_layers": [...]}}"""
    print(f"\n### ⛔ 4. DANH SÁCH MÃ BỊ LOẠI DO TRƯỢT CỔNG STRICT 3D — {len(rejections or {})} mã")
    if not rejections:
        print("   Không có mã nào bị loại (hoặc chưa có mã nào được đề xuất lưới).")
        return

    # 📊 Thống kê theo tầng (1 mã có thể trượt nhiều tầng)
    layer_count: Dict[int, int] = {}
    for rec in rejections.values():
        for lv in rec.get("failed_layers") or []:
            layer_count[lv] = layer_count.get(lv, 0) + 1
    if layer_count:
        print("   📊 " + " | ".join(f"T{lv} {_STRICT_LAYER_NAMES.get(lv, '?')}: {n}"
                                 for lv, n in sorted(layer_count.items())))

    def _n_fail(sym: str) -> int:
        return len(rejections[sym].get("failed_layers") or [])

    def _label(sym: str) -> str:
        n = _n_fail(sym)
        return f"{sym}×{n}" if n > 1 else sym

    # Gom theo lý do chính = lý do đầu tiên (tầng thấp nhất)
    groups: Dict[str, List[str]] = {}
    for sym, rec in rejections.items():
        reasons = rec.get("reasons") or ["Thiếu dữ liệu nến 3D"]
        groups.setdefault(_strict_group_key(reasons[0]), []).append(sym)

    def _layer_of(key: str) -> int:
        m = re.match(r"Tầng (\d)", key)
        return int(m.group(1)) if m else 9

    for key in sorted(groups, key=lambda k: (_layer_of(k), -len(groups[k]), k)):
        syms = sorted(groups[key], key=lambda s: (_n_fail(s), s))      # Mã gần đạt chuẩn đứng trước
        print(f"   ⛔ {key} ({len(syms)}): " + ", ".join(_label(s) for s in syms))

    near = sorted(s for s in rejections if _n_fail(s) == 1)
    if near:
        print(f"   🟡 Sát chuẩn — chỉ trượt 1 tầng ({len(near)}): "
              + ", ".join(f"{s}(T{rejections[s]['failed_layers'][0]})" for s in near))
    print("   ℹ️ MÃ×N = trượt N/4 tầng | nhóm theo tầng thấp nhất bị vi phạm")


_print_table = print_formatted_table   # tương thích tên cũ


DEFAULT_BOARD_CONFIG = {"top_spot": 2, "top_grid_tp2": 3, "top_wide_grid": 2, "fill_with_backup": True}


def _board_config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    cfg = dict(DEFAULT_BOARD_CONFIG)
    for k, v in (config or {}).items():
        if k in cfg and v is not None:
            cfg[k] = bool(v) if k == "fill_with_backup" else max(0, int(v))
    return cfg


def generate_summary_board(global_signals_pool: List[SignalRecord], btc_status: str = "",
                           live_data_map: Optional[Dict[str, Any]] = None, df_summary=None,
                           warning_results: Optional[List[Dict[str, Any]]] = None,
                           config: Optional[Dict[str, Any]] = None,
                           strict_3d_rejections: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """In BẢNG TỔNG KẾT TỐI ƯU (kèm Mã Dự Phòng lấp đầy slot). Trả về dict kết quả để tầng khác tái sử dụng.
    config: block `summary_board` trong settings.json
        {"top_spot": 2, "top_grid_tp2": 3, "top_wide_grid": 2, "fill_with_backup": true}
    strict_3d_rejections: sổ mã trượt cổng Strict 3D (core.macro_levels.get_strict_3d_rejections()).
        None → không in mục 4."""
    cfg = _board_config(config)
    fill = cfg["fill_with_backup"]
    result: Dict[str, Any] = {"spot": [], "grid_tp2": [], "wide_grid": [],
                              "spot_backup_symbols": set(), "grid_tp2_backup_symbols": set(),
                              "wide_grid_backup_symbols": set(),
                              "spot_is_backup": False, "grid_tp2_is_backup": False, "wide_grid_is_backup": False,
                              "spot_btc_locked": False}
    try:
        pool = list(global_signals_pool or [])
        _enrich(pool, live_data_map, df_summary, warning_results)

        btc_locked = bool(btc_status) and BTC_KILL_SWITCH_TEXT in btc_status
        result["spot_btc_locked"] = btc_locked
        b = classify_pool(pool, spot_enabled=True)

        print("\n" + "=" * 100)
        print("================ 🏁 BẢNG TỔNG KẾT TỐI ƯU ================")
        print("=" * 100)

        # Grid TP2 chọn trước → danh sách chống trùng cho Spot
        tp2_list, tp2_bk = pick_with_fallback(b["tp2_primary"], b["tp2_backup"], sort_grid_tp2, sort_grid_tp2,
                                              top_n=cfg["top_grid_tp2"], fill_with_backup=fill)
        tp2_symbols = [s.symbol for s in tp2_list]

        # 1. Spot (OCO) — BTC rủi ro cao: in cảnh báo khóa tín hiệu nhưng VẪN liệt kê ứng viên (Watchlist)
        print(f"\n### 🎯 1. CHIẾN LƯỢC SPOT (Lướt sóng OCO / Bắn tỉa 1 điểm) — Top {cfg['top_spot']}")
        if btc_locked:
            print("   ⚠️ BTC RỦI RO CAO - TẠM KHÓA TÍN HIỆU SPOT")
        spot_list, spot_bk = pick_with_fallback(b["spot_primary"], b["spot_backup"],
                                                sort_spot_primary, sort_spot_backup, tp2_symbols,
                                                top_n=cfg["top_spot"], fill_with_backup=fill)
        result["spot"], result["spot_backup_symbols"] = spot_list, spot_bk
        print_formatted_table(spot_list, "SPOT", spot_bk)

        # 2. Grid TP2
        print(f"\n### 🥅 2. CHIẾN LƯỢC GRID TP2 (Lưới đón Pullback 2-5 ngày) — Top {cfg['top_grid_tp2']}")
        result["grid_tp2"], result["grid_tp2_backup_symbols"] = tp2_list, tp2_bk
        print_formatted_table(tp2_list, "GRID_TP2", tp2_bk)

        # 3. Wide Grid 3D
        print(f"\n### 🦅 3. CHIẾN LƯỢC WIDE GRID 3D (Nuôi Cột cờ / Siêu sóng 1-3 tuần) — Top {cfg['top_wide_grid']}")
        wide_list, wide_bk = pick_with_fallback(b["wide_primary"], b["wide_backup"],
                                                sort_wide_primary, sort_wide_backup,
                                                top_n=cfg["top_wide_grid"], fill_with_backup=fill)
        result["wide_grid"], result["wide_grid_backup_symbols"] = wide_list, wide_bk
        print_formatted_table(wide_list, "WIDE_GRID_3D", wide_bk)

        # 4. Thống kê mã trượt cổng Strict 3D
        if strict_3d_rejections is not None:
            print_strict_3d_rejections(strict_3d_rejections)
            result["strict_3d_rejections"] = strict_3d_rejections

        # Cờ tương thích: True nếu bảng có ít nhất 1 mã dự phòng
        for k in ("spot", "grid_tp2", "wide_grid"):
            result[f"{k}_is_backup"] = bool(result[f"{k}_backup_symbols"])

        print("=" * 100)
    except Exception as e:
        print(f"Render Error (Summary Board): {e}")
    return result
