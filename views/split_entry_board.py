"""
Module: views/split_entry_board.py
Dự án: BinaC4 — Update v2.9 (Split-Entry: 2 Blocks 12x/5x + TP +12% / SL -22%)
Mục đích: BẢNG CHIẾN LƯỢC RẢI ĐINH PINGPONG (Split-Entry Tracker) — in ở cuối mỗi phiên báo cáo.

Nguồn dữ liệu (TRD §3.1) — gộp, khử trùng, phân thành 2 Nhóm (mỗi nhóm Top 3):
    🔥 Nhóm 1: Bắt Đáy & Hỗ Trợ Cứng (Isolated x12)
        - Bottom Fishing: Mã đạt `[HẠNG A1]` hoặc mốc OCO
        - Sắp xếp: Ưu tiên 1 - Giá kích hoạt gần đáy nhất (bottom_distance) | Ưu tiên 2 - Dòng tiền mạnh
    🌊 Nhóm 2: Dòng Tiền & Breakout (Isolated x5)
        - Pre-Breakout / Pullback Trend / Tiền Bạo Phát (DC3, DC4)
        - Sắp xếp: Ưu tiên 1 - Dòng tiền bạo phát mạnh nhất | Ưu tiên 2 - Khoảng cách chiết khấu (distance_pct)
"""

import logging
import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.split_entry_trigger import (
    build_symbol_data, calculate_optimal_trigger_detail, get_btc_rsi_1h, BTC_DUMP_RSI_THRESHOLD,
)

logger = logging.getLogger("CN4.SplitEntry.Board")

# ── Cấu hình TRD v2.9 ─────────────────────────────────────────────────────────
BOARD_TOP_N         = 3       # Tối đa 3 mã mỗi nhóm
PER_GROUP_TOP_N     = 3       # Top 3 cho Nhóm 1 (12x) và Top 3 cho Nhóm 2 (5x)
PER_STREAM_TOP_N    = 3       # Số ứng viên tối đa lấy từ mỗi luồng trước khi chấm trigger
PRE_BREAKOUT_D72    = 40.0    # delta_72h > 40
PULLBACK_TIERS      = ("Tier S", "Tier A")
ICON_NEAR_MAX_PCT   = 4.0     # |dist| ≤ 4%       → 🚨 #1
ICON_DIP_MAX_PCT    = 6.0     # 4% < |dist| ≤ 6%  → 👀 #2 ; > 6% → 💤 #3
GRID_SETUP_TEXT     = "TP +12% | SL -22%"

STREAM_BOTTOM      = "Bottom Fishing (A1)"
STREAM_PREBREAK    = "Pre-Breakout (Δ72H {d72:+.0f}đ)"
STREAM_PULLBACK    = "Pullback Trend ({tier})"
STREAM_SMART_FLOW  = "Tiền Bạo Phát {mf:.1f}x + R:R 1:{rr:.1f}"

SMART_FLOW_MIN_VOL = 2.0   # Tiền bạo phát > 2.0x
SMART_FLOW_MIN_RR  = 2.5   # R:R >= 1:2.5


def compute_split_grid_params(trig: float, tick_size: float = 0.0) -> Tuple[float, float, float, float]:
    """
    Thông số Grid chuẩn hình học cho Split-Entry (Isolated x10, 24 Lưới):
    - Lower Bound (L) : Trig * 0.90 (chiết khấu đón nhúng ~10%)
    - Upper Bound (U) : Trig * 1.20 (+20% chốt lời đỉnh lưới)
    - Stop-Loss (SL)  : L * 0.985 (cách đáy lưới 1.5% phòng vệ quét râu)
    - Take-Profit (TP): U
    """
    if trig <= 0:
        return 0.0, 0.0, 0.0, 0.0
    from core.split_entry_trigger import round_to_tick
    grid_low = round_to_tick(trig * 0.90, tick_size)
    grid_up = round_to_tick(trig * 1.20, tick_size)
    grid_sl = round_to_tick(grid_low * 0.985, tick_size)
    grid_tp = grid_up
    return grid_low, grid_up, grid_sl, grid_tp


def _clean(sym: Any) -> str:
    return str(sym or "").upper().replace("/USDT", "").replace("USDT", "").strip()


def _tier_of(tier_raw: Any) -> str:
    """Chuẩn hóa Tier: 'Tier A-' tách biệt khỏi 'Tier A' (A- không thuộc nhóm tinh hoa)."""
    t = str(tier_raw or "").replace("💎", "").strip()
    for prefix in ("Tier S", "Tier A-", "Tier A", "Tier B", "Tier C", "Tier F"):
        if prefix in t:
            return prefix
    return t


def fmt_price(p: Optional[float], tick_size: float = 0.0) -> str:
    """Hiển thị đúng số lẻ theo tick_size; thiếu tick → 4 chữ số có nghĩa."""
    if p is None or p <= 0:
        return "-"
    if tick_size and tick_size > 0:
        decimals = max(0, -int(math.floor(math.log10(tick_size) + 1e-9)))
    elif p >= 1:
        decimals = 4
    else:
        decimals = min(12, -int(math.floor(math.log10(p))) + 3)
    s = f"{p:.{decimals}f}"
    if "." in s and not tick_size:
        s = s.rstrip("0").rstrip(".")
    return s


def rank_icon(distance_pct: float, rank_idx: int) -> str:
    """TRD v2.9 — Decorator icon theo khoảng cách và thứ tự hạng trong nhóm."""
    d = abs(distance_pct)
    if d <= ICON_NEAR_MAX_PCT:
        ic = "🚨"
    elif d <= ICON_DIP_MAX_PCT:
        ic = "👀"
    else:
        ic = "💤"
    return f"{ic} #{rank_idx}"


def status_icon(distance_pct: float) -> str:
    """TRD §3.2 — Decorator theo |distance_pct|."""
    d = abs(distance_pct)
    if d <= ICON_NEAR_MAX_PCT:
        return "🚨 #1 Sắp Khớp Lệnh"
    if d <= ICON_DIP_MAX_PCT:
        return "👀 #2 Chờ Nhúng Sâu"
    return "💤 #3 Chờ Đạp Mạnh"


def parse_grid_line_params(line: str) -> Optional[Tuple[float, float, float, float, int]]:
    """Trích xuất (lower, upper, trig, sl, tp, grids) từ grid_tp2_line nếu có."""
    if not line:
        return None
    try:
        import re
        m_range = re.search(r"([\d.]+)\s*-\s*([\d.]+)", line)
        m_grids = re.search(r"(\d+)\s*L", line, re.IGNORECASE)
        m_trig = re.search(r"Trig:\s*([\d.]+)", line, re.IGNORECASE)
        m_sl = re.search(r"SL:\s*([\d.]+)", line, re.IGNORECASE)
        m_tp = re.search(r"TP:\s*([\d.]+)", line, re.IGNORECASE)
        if m_range and m_trig:
            lower = float(m_range.group(1))
            upper = float(m_range.group(2))
            trig = float(m_trig.group(1))
            sl = float(m_sl.group(1)) if m_sl else lower * 0.985
            tp = float(m_tp.group(1)) if m_tp else upper
            grids = int(m_grids.group(1)) if m_grids else 24
            return lower, upper, trig, sl, tp, grids
    except Exception:
        pass
    return None


def extract_smart_flow_candidates(
    top_money_flow: Optional[Iterable[Any]] = None,
    signals_pool: Optional[Iterable[Any]] = None,
    dc3_states: Optional[Iterable[Any]] = None,
    dc4_states: Optional[Iterable[Any]] = None,
    min_vol: float = SMART_FLOW_MIN_VOL,
    min_rr: float = SMART_FLOW_MIN_RR,
    top_n: int = PER_STREAM_TOP_N
) -> Tuple[List[Tuple[str, float, float]], Dict[str, Tuple[float, float, float, float, int]]]:
    """
    TRD v2.8 §3.1 (Luồng 4 - MỚI):
    Ưu tiên lấy trực tiếp từ `top_money_flow` của BẢNG TỔNG KẾT TỐI ƯU (đã qua Gatekeeper bóp cò).
    Nếu thiếu hoặc không có, fallback quét từ signals_pool / dc3_states / dc4_states thỏa mãn:
    - Tín hiệu Volume: Tiền bạo phát > 2.0x
    - Biên độ an toàn: Tỷ lệ Reward/Risk R:R >= 1:2.5
    Trả về ([(symbol, money_flow, rr_ratio), ...], {symbol: grid_params}).
    """
    cands: Dict[str, Tuple[float, float]] = {}
    grid_params_map: Dict[str, Tuple[float, float, float, float, int]] = {}

    # 1. Ưu tiên số 1: Lấy trực tiếp từ top_money_flow của Bảng Tổng Kết Tối Ưu
    for sig in top_money_flow or []:
        sym = _clean(getattr(sig, "symbol", "") or (sig.get("symbol", "") if isinstance(sig, dict) else ""))
        if not sym:
            continue
        mf = float(getattr(sig, "money_flow", 0.0) or (sig.get("money_flow", 0.0) if isinstance(sig, dict) else 0.0) or 0.0)
        rr = float(getattr(sig, "rr_ratio", 0.0) or (sig.get("rr_ratio", 0.0) if isinstance(sig, dict) else 0.0) or 0.0)
        if mf > min_vol and rr >= min_rr:
            cands[sym] = (mf, rr)
            g_line = getattr(sig, "grid_tp2_line", "") or (sig.get("grid_tp2_line", "") if isinstance(sig, dict) else "")
            parsed = parse_grid_line_params(g_line)
            if parsed:
                grid_params_map[sym] = parsed

    # 2. Quét từ signals_pool (SignalRecord của console_renderer)
    for sig in signals_pool or []:
        eng = getattr(sig, "engine_source", "") or (sig.get("engine_source", "") if isinstance(sig, dict) else "")
        if eng in ("DC3", "DC4"):
            sym = _clean(getattr(sig, "symbol", "") or (sig.get("symbol", "") if isinstance(sig, dict) else ""))
            if not sym or sym in cands:
                continue
            mf = float(getattr(sig, "money_flow", 0.0) or (sig.get("money_flow", 0.0) if isinstance(sig, dict) else 0.0) or 0.0)
            rr = float(getattr(sig, "rr_ratio", 0.0) or (sig.get("rr_ratio", 0.0) if isinstance(sig, dict) else 0.0) or 0.0)
            if mf > min_vol and rr >= min_rr:
                cands[sym] = (mf, rr)
                g_line = getattr(sig, "grid_tp2_line", "") or (sig.get("grid_tp2_line", "") if isinstance(sig, dict) else "")
                parsed = parse_grid_line_params(g_line)
                if parsed:
                    grid_params_map[sym] = parsed

    # 3. Bổ sung từ dc3_states (Momentum Breakout) nếu chưa có
    for st in (dc3_states or []):
        sym = _clean(getattr(st, "symbol", ""))
        if not sym or sym in cands:
            continue
        score_ctx = getattr(st, "scores", {}).get("DC3") if hasattr(st, "scores") and isinstance(st.scores, dict) else None
        setup = getattr(score_ctx, "entry_setup1", None) if score_ctx else None
        rr = float(getattr(setup, "rr_ratio", 0.0) or 0.0) if setup else 0.0
        d3 = getattr(st, "d3", {}) or {}
        expl = (d3.get("explosive") or {}).get("ratio", 0.0) if isinstance(d3, dict) else 0.0
        try:
            from views.summary_board import extract_money_flow
            mf = max(extract_money_flow(getattr(score_ctx, "action_label", ""), getattr(st, "money_flow_tag", "")), float(expl or 0.0))
        except Exception:
            mf = float(expl or 0.0)
        if mf > min_vol and rr >= min_rr:
            cands[sym] = (mf, rr)

    # 4. Bổ sung từ dc4_states (Hot Trend Pullback) nếu chưa có
    for st in (dc4_states or []):
        sym = _clean(getattr(st, "symbol", ""))
        if not sym or sym in cands:
            continue
        score_ctx = getattr(st, "scores", {}).get("DC4") if hasattr(st, "scores") and isinstance(st.scores, dict) else None
        setup = getattr(score_ctx, "entry_setup1", None) if score_ctx else None
        rr = float(getattr(setup, "rr_ratio", 0.0) or 0.0) if setup else 0.0
        d3 = getattr(st, "d3", {}) or {}
        try:
            from views.summary_board import extract_money_flow
            mf = max(extract_money_flow(getattr(score_ctx, "action_label", ""), getattr(st, "money_flow_tag", "")), float(expl or 0.0))
        except Exception:
            mf = float(expl or 0.0)
        if mf > min_vol and rr >= min_rr:
            cands[sym] = (mf, rr)

    sorted_cands = sorted(
        [(sym, mf, rr) for sym, (mf, rr) in cands.items()],
        key=lambda x: (x[1], x[2]),
        reverse=True
    )
    return sorted_cands[:top_n], grid_params_map


# ── Thu thập ứng viên từ 4 luồng tinh hoa ─────────────────────────────────────
def collect_candidates(convergence_ranks: Optional[Dict[str, str]] = None,
                       pre_breakout_pool: Optional[Iterable[Dict[str, Any]]] = None,
                       trust_profiles: Optional[Iterable[Dict[str, Any]]] = None,
                       top_money_flow: Optional[Iterable[Any]] = None,
                       signals_pool: Optional[Iterable[Any]] = None,
                       dc3_states: Optional[Iterable[Any]] = None,
                       dc4_states: Optional[Iterable[Any]] = None,
                       per_stream_top: int = PER_STREAM_TOP_N,
                       grid_presets_out: Optional[Dict[str, Any]] = None) -> Dict[str, List[str]]:
    """Trả về {symbol: [nhãn luồng, ...]} theo thứ tự ưu tiên luồng 1 → 2 → 3 → 4."""
    out: Dict[str, List[str]] = {}

    def _add(sym: str, label: str):
        if sym:
            out.setdefault(sym, [])
            if label not in out[sym]:
                out[sym].append(label)

    # 1. Bottom Fishing — [HẠNG A1] (dict đã sắp xếp ưu tiên; khử key trùng clean/raw)
    a1, seen = [], set()
    for sym, rank in (convergence_ranks or {}).items():
        c = _clean(sym)
        if "HẠNG A1" in str(rank) and c not in seen:
            seen.add(c)
            a1.append(c)
    for c in a1[:per_stream_top]:
        _add(c, STREAM_BOTTOM)

    # 2. Pre-Breakout — delta_72h > 40, sắp xếp giảm dần
    pre = []
    for it in pre_breakout_pool or []:
        try:
            d72 = float(it.get("delta_72h", it.get("delta_4", 0.0)) or 0.0)
        except (TypeError, ValueError):
            continue
        if d72 > PRE_BREAKOUT_D72 and _tier_of(it.get("tier")) != "Tier F":
            pre.append((_clean(it.get("symbol")), d72))
    pre.sort(key=lambda x: x[1], reverse=True)
    for c, d72 in pre[:per_stream_top]:
        _add(c, STREAM_PREBREAK.format(d72=d72))

    # 3. Pullback Trend — Tier S / Tier A, điểm tín nhiệm giảm dần
    pb = []
    for p in trust_profiles or []:
        tier = _tier_of(p.get("tier"))
        if tier in PULLBACK_TIERS:
            try:
                sc = float(p.get("total_score", 0.0) or 0.0)
            except (TypeError, ValueError):
                sc = 0.0
            pb.append((_clean(p.get("symbol")), tier, sc))
    pb.sort(key=lambda x: x[2], reverse=True)
    for c, tier, _ in pb[:per_stream_top]:
        _add(c, STREAM_PULLBACK.format(tier=tier))

    # 4. Dòng Tiền Bạo Phát & R:R (MỚI - TRD v2.8) — Ưu tiên lấy từ BẢNG TỔNG KẾT TỐI ƯU
    smart_cands, custom_grids = extract_smart_flow_candidates(
        top_money_flow=top_money_flow,
        signals_pool=signals_pool,
        dc3_states=dc3_states,
        dc4_states=dc4_states,
        top_n=per_stream_top
    )
    if grid_presets_out is not None and custom_grids:
        grid_presets_out.update(custom_grids)
    for c, mf, rr in smart_cands:
        _add(c, STREAM_SMART_FLOW.format(mf=mf, rr=rr))

    return out


def build_oco1_map(dc2_states: Optional[Iterable[Any]]) -> Dict[str, float]:
    """{symbol: giá Buy Limit OCO-1} từ kết quả Engine 2 (Pullback Sniper)."""
    out: Dict[str, float] = {}
    for st in dc2_states or []:
        try:
            ctx = st.scores.get("DC2") if isinstance(st.scores, dict) else None
            setup1 = getattr(ctx, "entry_setup1", None) if ctx else None
            price = float(getattr(setup1, "entry_price", 0) or 0) if setup1 else 0.0
            if price > 0:
                out[_clean(st.symbol)] = price
        except Exception:
            continue
    return out


def _live_price(sym: str, live_data_map: Optional[Dict[str, Any]]) -> Optional[float]:
    info = (live_data_map or {}).get(f"{sym}USDT") or (live_data_map or {}).get(sym) or {}
    try:
        v = float((info.get("last_price") if isinstance(info, dict) else getattr(info, "last_price", 0)) or 0)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


# ── Phân loại 2 nhóm & Sắp xếp (TRD v2.9) ───────────────────────────────────
def partition_and_sort_groups(
    rows: List[Dict[str, Any]],
    per_group_top: int = PER_GROUP_TOP_N
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    TRD v2.9: Phân loại 2 nhóm đòn bẩy:
    - Nhóm 1: Bắt Đáy & Hỗ Trợ Cứng (Isolated x12)
      * Sắp xếp: Ưu tiên 1 - Giá kích hoạt gần đáy nhất (bottom_distance nhỏ nhất) | Ưu tiên 2 - Dòng tiền mạnh
    - Nhóm 2: Dòng Tiền & Breakout (Isolated x5)
      * Sắp xếp: Ưu tiên 1 - Dòng tiền bạo phát mạnh nhất | Ưu tiên 2 - Khoảng cách chiết khấu (distance_pct)
    """
    import re
    group_12x = []
    group_5x = []

    for item in rows:
        note = item.get('note') or f"{' + '.join(item.get('streams', []))} {item.get('support_label', '')}"
        item['note'] = note
        trigger = float(item.get('optimal_trigger', 0.0) or 0.0)
        tick = float(item.get('tick_size', 0.0) or 0.0)

        if item.get("custom_grid"):
            grid_low, grid_up, _trig_preset, _grid_sl, _grid_tp, grids = item["custom_grid"]
        else:
            grid_low, grid_up, _grid_sl, _grid_tp = compute_split_grid_params(trigger, tick)
            grids = 24

        item['grid_lower'] = grid_low
        item['grid_upper'] = grid_up
        item['grids'] = grids

        # TÍNH TOÁN TIÊU CHÍ 1: Độ sát đáy (Càng nhỏ càng sát đáy lưới)
        lower_limit = grid_low
        item['bottom_distance'] = (trigger - lower_limit) / lower_limit if lower_limit > 0 else 999.0
        item['bottom_pct'] = item['bottom_distance'] * 100.0

        # TÍNH TOÁN TIÊU CHÍ 2: Hệ số dòng tiền (Bóc tách con số 2.5x, 4.1x từ Note hoặc item)
        vol_match = re.search(r'Tiền Bạo Phát\s*(\d+(?:\.\d+)?)x', note, re.IGNORECASE)
        if vol_match:
            item['money_flow_multiplier'] = float(vol_match.group(1))
        else:
            item['money_flow_multiplier'] = float(item.get('money_flow', 0.0) or 0.0)

        # Phân nhóm: Bắt đáy / Hỗ trợ cứng vs Dòng tiền / Breakout
        is_bottom = "Bottom Fishing" in note or "OCO" in note or "A1" in note
        item['is_bottom_fishing'] = is_bottom

        if is_bottom:
            item['group'] = '12x'
            item['leverage_str'] = 'Isolated x12'
            group_12x.append(item)
        else:
            item['group'] = '5x'
            item['leverage_str'] = 'Isolated x5'
            group_5x.append(item)

    # 2. SẮP XẾP (SORTING) ƯU TIÊN THEO YÊU CẦU USER
    # Nhóm 12x: Ưu tiên 1 - Giá kích hoạt gần đáy nhất (bottom_distance nhỏ nhất) | Ưu tiên 2 - Dòng tiền mạnh
    group_12x.sort(key=lambda x: (x['bottom_distance'], -x['money_flow_multiplier']))

    # Nhóm 5x: Ưu tiên 1 - Dòng tiền bạo phát mạnh nhất | Ưu tiên 2 - Khoảng cách chiết khấu (distance_pct)
    group_5x.sort(key=lambda x: (-x['money_flow_multiplier'], -x.get('distance_pct', 0.0)))

    # 3. Rút gọn mỗi nhóm lấy đúng Top N (mặc định Top 3)
    top_12x = group_12x[:per_group_top]
    top_5x = group_5x[:per_group_top]

    for idx, r in enumerate(top_12x, 1):
        r['rank_idx'] = idx
        r['status_icon'] = rank_icon(r.get('distance_pct', 0.0), idx)

    for idx, r in enumerate(top_5x, 1):
        r['rank_idx'] = idx
        r['status_icon'] = rank_icon(r.get('distance_pct', 0.0), idx)

    return top_12x, top_5x


# ── Tính toán & Thu thập hàng ─────────────────────────────────────────────────
def build_split_entry_rows(candidates: Dict[str, List[str]],
                           live_data_map: Optional[Dict[str, Any]] = None,
                           oco1_map: Optional[Dict[str, float]] = None,
                           btc_rsi_1h: Optional[float] = None,
                           top_n: Optional[int] = None,
                           data_builder=build_symbol_data,
                           grid_presets: Optional[Dict[str, Any]] = None,
                           per_group_top: int = PER_GROUP_TOP_N) -> List[Dict[str, Any]]:
    """Tính optimal trigger cho từng ứng viên → phân 2 nhóm 12x & 5x → sort theo TRD v2.9."""
    rows: List[Dict[str, Any]] = []
    for sym, streams in candidates.items():
        try:
            data = data_builder(sym, _live_price(sym, live_data_map), btc_rsi_1h,
                                (oco1_map or {}).get(sym))
            det = calculate_optimal_trigger_detail(data)
            if det.get("optimal_trigger") is None:
                continue
            r_item = {
                "symbol": sym,
                "streams": streams,
                "live_price": det["live_price"],
                "optimal_trigger": det["optimal_trigger"],
                "distance_pct": det["distance_pct"],
                "support_label": det["selected_label"],
                "flags": det["flags"],
                "is_market_dump": det["is_market_dump"],
                "tick_size": float(data.get("tick_size") or 0.0),
            }
            if grid_presets and sym in grid_presets:
                r_item["custom_grid"] = grid_presets[sym]
            rows.append(r_item)
        except Exception as e:
            logger.debug(f"[SplitEntry] {sym}: lỗi tính trigger: {e}")

    top_12x, top_5x = partition_and_sort_groups(rows, per_group_top=per_group_top)
    return top_12x + top_5x


# ── Render Markdown ───────────────────────────────────────────────────────────
def render_split_entry_table(rows: List[Dict[str, Any]], btc_rsi_1h: Optional[float] = None) -> str:
    """TRD §3.3 — Bảng Markdown phân tách 2 nhóm theo chuẩn v2.9 (Isolated x12 & Isolated x5)."""
    if btc_rsi_1h is None:
        btc_ctx = "BTC RSI 1H: N/A"
    elif btc_rsi_1h < BTC_DUMP_RSI_THRESHOLD:
        btc_ctx = f"🔴 BTC RSI 1H = {btc_rsi_1h:.1f} (< {BTC_DUMP_RSI_THRESHOLD:.0f} → Market Dump: lùi mốc về MA25 3D)"
    else:
        btc_ctx = f"🟢 BTC RSI 1H = {btc_rsi_1h:.1f} (Ổn định)"

    sep_line = "=" * 152
    sub_sep_line = "-" * 152

    # Phân nhóm nếu chưa phân
    has_groups = any(r.get('group') in ('12x', '5x') for r in rows)
    if has_groups:
        top3_12x = [r for r in rows if r.get('group') == '12x'][:PER_GROUP_TOP_N]
        top3_5x = [r for r in rows if r.get('group') == '5x'][:PER_GROUP_TOP_N]
    else:
        top3_12x, top3_5x = partition_and_sort_groups(rows, per_group_top=PER_GROUP_TOP_N)

    lines = [
        "",
        "📌 BẢNG CHIẾN LƯỢC RẢI ĐINH PINGPONG (SPLIT-ENTRY TRACKER) - v2.9",
        f"{btc_ctx} | Nguồn Setup: Kế thừa GRID TP2 | Rule mới: TP +12% | SL -22%",
        sep_line,
    ]

    header_cols = (
        "| Hạng | Mã Bot | Live | Kích Hoạt (Trig) | "
        "Setup Grid (24 Lưới) & Tỷ Lệ Cố Định | Trạng Thái / Dòng Tiền & Hỗ Trợ"
    )
    header_align = "| :--- | :--- | :--- | :--- | :--- | :--- |"

    # --- NHÓM 1: BẮT ĐÁY & HỖ TRỢ CỨNG (ĐÒN BẨY: ISOLATED x12) ---
    lines.extend([
        "",
        "🔥 NHÓM 1: BẮT ĐÁY & HỖ TRỢ CỨNG (ĐÒN BẨY: ISOLATED x12)",
        "* Tiêu chí: Lệnh OCO đáy cứng. Ưu tiên Giá kích hoạt (Trigger) ép sát nhất vào Đáy lưới.",
        sub_sep_line,
        header_cols,
        header_align,
    ])
    if top3_12x:
        for r in top3_12x:
            tick = r.get("tick_size", 0.0)
            trig = r["optimal_trigger"]
            grid_low = r.get("grid_lower", 0.0)
            grid_up = r.get("grid_upper", 0.0)
            grid_str = f"Dải L={fmt_price(grid_low, tick)} - U={fmt_price(grid_up, tick)} | TP +12% | SL -22%"
            status_base = f"{' + '.join(r['streams'])} · Hỗ trợ: {r['support_label']}" if r.get('support_label') else ' + '.join(r['streams'])
            status_str = f"{status_base} (Cách đáy L {r.get('bottom_pct', 0.0):.1f}%)"
            lines.append(
                f"| {r['status_icon']} | {r['symbol']}/USDT | "
                f"{fmt_price(r['live_price'], tick)} | {fmt_price(trig, tick)} | "
                f"{grid_str} | {status_str}"
            )
    else:
        lines.append("   (Không có mã nào đạt tiêu chí Bắt đáy trong phiên này)")

    lines.extend([
        "",
        sep_line,
    ])

    # --- NHÓM 2: DÒNG TIỀN & BREAKOUT (ĐÒN BẨY: ISOLATED x5) ---
    lines.extend([
        "",
        "🌊 NHÓM 2: DÒNG TIỀN & BREAKOUT (ĐÒN BẨY: ISOLATED x5)",
        "* Tiêu chí: Đánh theo sóng đẩy. Ưu tiên Hệ số Tiền bạo phát (Volume Mồi) mạnh nhất.",
        sub_sep_line,
        header_cols,
        header_align,
    ])
    if top3_5x:
        for r in top3_5x:
            tick = r.get("tick_size", 0.0)
            trig = r["optimal_trigger"]
            grid_low = r.get("grid_lower", 0.0)
            grid_up = r.get("grid_upper", 0.0)
            grid_str = f"Dải L={fmt_price(grid_low, tick)} - U={fmt_price(grid_up, tick)} | TP +12% | SL -22%"
            status_str = f"{' + '.join(r['streams'])} · Hỗ trợ: {r['support_label']}" if r.get('support_label') else ' + '.join(r['streams'])
            lines.append(
                f"| {r['status_icon']} | {r['symbol']}/USDT | "
                f"{fmt_price(r['live_price'], tick)} | {fmt_price(trig, tick)} | "
                f"{grid_str} | {status_str}"
            )
    else:
        lines.append("   (Không có mã nào đạt tiêu chí Dòng tiền & Breakout trong phiên này)")

    lines.extend([
        "",
        sep_line,
    ])
    return "\n".join(lines)


def generate_split_entry_board(convergence_ranks: Optional[Dict[str, str]] = None,
                               pre_breakout_pool: Optional[Iterable[Dict[str, Any]]] = None,
                               trust_profiles: Optional[Iterable[Dict[str, Any]]] = None,
                               top_money_flow: Optional[Iterable[Any]] = None,
                               dc2_states: Optional[Iterable[Any]] = None,
                               dc3_states: Optional[Iterable[Any]] = None,
                               dc4_states: Optional[Iterable[Any]] = None,
                               signals_pool: Optional[Iterable[Any]] = None,
                               live_data_map: Optional[Dict[str, Any]] = None,
                               btc_rsi_1h: Optional[float] = None,
                               print_output: bool = True) -> List[Dict[str, Any]]:
    """Entry point: gom 4 luồng tinh hoa → tính trigger → in bảng. Trả về rows để tầng khác tái sử dụng."""
    rows: List[Dict[str, Any]] = []
    try:
        if btc_rsi_1h is None:
            btc_rsi_1h = get_btc_rsi_1h()
        grid_presets: Dict[str, Any] = {}
        candidates = collect_candidates(
            convergence_ranks=convergence_ranks,
            pre_breakout_pool=pre_breakout_pool,
            trust_profiles=trust_profiles,
            top_money_flow=top_money_flow,
            signals_pool=signals_pool,
            dc3_states=dc3_states,
            dc4_states=dc4_states,
            grid_presets_out=grid_presets,
        )
        oco_map = build_oco1_map(dc2_states)
        for sym, preset in grid_presets.items():
            if sym not in oco_map and preset:
                oco_map[sym] = preset[2] # trig từ preset
        rows = build_split_entry_rows(candidates, live_data_map, oco_map, btc_rsi_1h, grid_presets=grid_presets)
        if print_output:
            print(render_split_entry_table(rows, btc_rsi_1h))
    except Exception as e:
        logger.error(f"Render Error (Bảng Rải Đinh): {e}", exc_info=True)
    return rows
