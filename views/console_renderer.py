import pandas as pd
from typing import List, Dict, Any
from models.market_state import SymbolState, ScoreContext, GridContext, EarlyWarningContext, EntrySetupContext, MacroState

class ConsoleRenderer:
    """
    Lớp chuyên trách xử lý hiển thị ra màn hình Console.
    Đảm bảo Fail-Safe bằng cách bọc toàn bộ trong try-except.
    """
    
    @staticmethod
    def fmt_price(price: float) -> str:
        """Định dạng giá thông minh, tự động cắt số 0"""
        if price >= 10:
            return f"{price:.2f}"
        elif price >= 1:
            return f"{price:.3f}"
        elif price >= 0.1:
            return f"{price:.4f}"
        elif price >= 0.01:
            return f"{price:.5f}"
        else:
            return f"{price:.6f}"

    def render_early_warning_matrix(self, warning_results: List[Dict[str, Any]], total_scanned: int):
        """Render Bảng Cảnh Báo Sớm"""
        try:
            print("\n" + "!" * 125)
            print(f"🚨 HỆ THỐNG CẢNH BÁO SỚM & RỦI RO SẬP (EARLY WARNING MATRIX)")
            print("!" * 125)
            print(f"| {'Mức Độ (Level)':<50} | {'Tín Hiệu (Trigger)':<40} | {'Danh Sách Mã (Symbols)'}")
            print(f"|{'-'*52}|{'-'*42}|{'-'*60}")
            
            filtered_warnings = [r for r in warning_results if r.get('level') in (1, 2, 3)]
            if filtered_warnings:
                from collections import defaultdict
                grouped = defaultdict(list)
                for res in filtered_warnings:
                    key = (res.get('label', ''), res.get('trigger', ''))
                    grouped[key].append(res.get('symbol', '').replace('/USDT', ''))
                
                for (lbl, trig), symbols in grouped.items():
                    sym_str = ", ".join(symbols)
                    count = len(symbols)
                    lbl_with_count = f"{lbl} ({count}/{total_scanned})"
                    print(f"| {lbl_with_count:<50} | {trig:<40} | {sym_str}")
            else:
                print(f"| {'(Không có mã nào)':<50} | {'-':<40} | {'-'}")
            print("!" * 125 + "\n")
        except Exception as e:
            print(f"Render Error (Early Warning): {e}")

    def render_darvas_grid(self, symbol_states: List[SymbolState]):
        """Render Động Cơ 1: Darvas Grid"""
        try:
            print("\n" + "=" * 115)
            print(f"📦 ĐỘNG CƠ 1: DARVAS GRID (Dành cho Chiến lược Phòng thủ Móng nhà)")
            print("=" * 115)
            print(f"{'Mã (Symbol)':<15} | {'Tổng Điểm':<10} | {'Trạng Thái Bảng 1':<35} | {'Hành Động'}")
            print("| --- | --- | --- | --- |")
            
            for state in symbol_states:
                score_ctx = state.scores.get("DC1")
                if not score_ctx:
                    continue
                
                sym = state.symbol
                score = score_ctx.total_score
                act = score_ctx.action_label
                safe_tag = state.safety_tag
                
                print(f"{sym:<15} | {score:<10} | {safe_tag:<35} | {act}")
                if score >= 60 and score_ctx.grid_setup:
                    g_setup = score_ctx.grid_setup
                    sl = g_setup.stop_loss
                    tp = g_setup.take_profit
                    if g_setup.is_dual_grid:
                        print(f"  ↳ ⚙️ DUAL GRID: [{sym}] SL = {sl} | TP = {tp}")
                        print(f"     ├── G1 (Bắt đáy): {g_setup.g1_lower} - {g_setup.g1_upper} ({g_setup.g1_grids} Lưới) [70% Vốn]")
                        print(f"     └── G2 (Đột phá): {g_setup.g2_lower} - {g_setup.g2_upper} ({g_setup.g2_grids} Lưới) [30% Vốn]")
                    else:
                        print(f"  ↳ ⚙️ SETUP GRID: [{sym}] Lower = {g_setup.lower_price} | Uper = {g_setup.upper_price} | Grids = {g_setup.grid_quantity}| SL = {sl} | TP = {tp}")
                        
            print("=" * 115 + "\n")
        except Exception as e:
            print(f"Render Error (Darvas Grid): {e}")

    def render_pullback_sniper(self, symbol_states: List[SymbolState]):
        """Render Động Cơ 2: Pullback Sniper"""
        try:
            print("\n" + "=" * 175)
            print(f"🎯 BẢNG CHẤM ĐIỂM PULLBACK SNIPER (ĐỘNG CƠ 2 - TÌM LỆNH THỰC THI CHÍNH XÁC)")
            print("=" * 175)
            print(f"{'Mã (Symbol)':<15} | {'Tổng Điểm':<10} | {'Trạng Thái Bảng 1':<35} | {'C1 (Hội Tụ)':<12} | {'C2 (Vol)':<10} | {'C3 (Sổ Lệnh)':<12} | {'C4 (R/R)':<10} | {'Hành Động'}")
            print("| --- | --- | --- | --- | --- | --- | --- | --- |")
            
            for state in symbol_states:
                score_ctx = state.scores.get("DC2")
                if not score_ctx:
                    continue
                    
                sym = state.symbol
                score = score_ctx.total_score
                act = score_ctx.action_label
                safe_tag = state.safety_tag
                
                c1 = score_ctx.c1_score
                c2 = score_ctx.c2_score
                c3 = score_ctx.c3_score
                c4 = score_ctx.c4_score
                
                print(f"{sym:<15} | {score:<10} | {safe_tag:<35} | {c1:<12} | {c2:<10} | {c3:<12} | {c4:<10} | {act}")
                
                # In Early Warning
                if score_ctx.early_warning:
                    ew = score_ctx.early_warning
                    force_tag = " [⚠️ FORCE-CON]" if ew.force_conservative else ""
                    print(f"   ↳ 🛡️ EW Sniper [{sym}]: {ew.ew_label}{force_tag}")
                    print(f"      PB Score={ew.pullback_score}/100 "
                          f"| C1-Wick={ew.c1_wick_score}đ C2-DryUp={ew.c2_micro_dryup_score}đ "
                          f"C3-Momentum={ew.c3_macro_momentum_score}đ C4-TakerBuy={ew.c4_taker_buy_score}đ")
                    if ew.ew_level == 1:
                        triggers = " | ".join(ew.triggers)
                        print(f"   ↳ ⛔ [EW CẤP 1 REJECT] {triggers}")

                # In Entry Setup (OCO)
                if score_ctx.entry_setup1:
                    s1 = score_ctx.entry_setup1
                    sl1_pct = (s1.entry_price - s1.sl_price) / s1.entry_price * 100 if s1.entry_price else 0
                    tp1_pct = (s1.tp1_price - s1.entry_price) / s1.entry_price * 100 if s1.entry_price else 0
                    print(f"   ↳ OCO-1 [{sym}] Buy={self.fmt_price(s1.entry_price)} "
                          f"| SL={self.fmt_price(s1.sl_price)}(-{sl1_pct:.1f}%) "
                          f"| TP={self.fmt_price(s1.tp1_price)}(+{tp1_pct:.1f}%) "
                          f"| R/R=1:{s1.rr_ratio:.1f}")
                
                if score_ctx.entry_setup2:
                    s2 = score_ctx.entry_setup2
                    sl2_pct = (s2.entry_price - s2.sl_price) / s2.entry_price * 100 if s2.entry_price else 0
                    tp2_pct = (s2.tp1_price - s2.entry_price) / s2.entry_price * 100 if s2.entry_price else 0
                    print(f"   ↳ OCO-2 [{sym}] Buy={self.fmt_price(s2.entry_price)} "
                          f"| SL={self.fmt_price(s2.sl_price)}(-{sl2_pct:.1f}%) "
                          f"| TP={self.fmt_price(s2.tp1_price)}(+{tp2_pct:.1f}%) "
                          f"| R/R=1:{s2.rr_ratio:.1f} "
                          f"| Trailing={s2.trailing_trigger}")
                
                if score >= 70:
                    print("-" * 175)
            
            print("=" * 175 + "\n")
        except Exception as e:
            print(f"Render Error (Pullback Sniper): {e}")

    def render_momentum_breakout(self, symbol_states: List[SymbolState], btc_gate_label: str):
        """Render Động Cơ 3: Momentum Breakout"""
        try:
            print("\n" + "=" * 175)
            print(f"🚀 BẢNG CHẤM ĐIỂM MOMENTUM BREAKOUT (ĐỘNG CƠ 3 - SĂN BỨT PHÁ ĐỘNG LƯỢNG)")
            print(f"   📡 BTC 1H Gate: {btc_gate_label}")
            print("=" * 175)
            print(f"{'Mã (Symbol)':<15} | {'Tổng Điểm':<10} | {'C1 (PriceAct)':<25} | {'C2 (Volume)':<25} | {'C3 (OrderBook)':<25} | {'C4 (R/R)':<25} | {'Bonus (Taker)':<25} | {'Hành Động'}")
            print("| --- | --- | --- | --- | --- | --- | --- | --- |")
            
            for state in symbol_states:
                score_ctx = state.scores.get("DC3")
                if not score_ctx:
                    continue
                
                sym = state.symbol
                score = score_ctx.total_score
                act = score_ctx.action_label
                c1 = score_ctx.c1_score
                c2 = score_ctx.c2_score
                c3 = score_ctx.c3_score
                c4 = score_ctx.c4_score
                bonus = score_ctx.bonus_score
                
                print(f"{sym:<15} | {score:<10} | {c1:<25} | {c2:<25} | {c3:<25} | {c4:<25} | {bonus:<25} | {act}")
                
                # In Entry Setup
                if score_ctx.entry_setup1:
                    setup = score_ctx.entry_setup1
                    if setup.setup_type == "MOCK_SCALE_OUT":
                        sl_pct = (setup.entry_price - setup.sl_price) / setup.entry_price * 100 if setup.entry_price else 0
                        tp1_pct = (setup.tp1_price - setup.entry_price) / setup.entry_price * 100 if setup.entry_price else 0
                        tp2_pct = (setup.tp2_price - setup.entry_price) / setup.entry_price * 100 if setup.entry_price else 0
                        print(f"   ↳ ⚙️ SETUP [{sym}] Entry = {self.fmt_price(setup.entry_price)} | SL = {self.fmt_price(setup.sl_price)} (-{sl_pct:.1f}%) | R/R = 1:{setup.rr_ratio:.1f}")
                        print(f"       📄 [MOCK SCALE-OUT] TP1={self.fmt_price(setup.tp1_price)} (+{tp1_pct:.1f}%) | TP2={self.fmt_price(setup.tp2_price)} (+{tp2_pct:.1f}%)")
                    else:
                        sl_pct = (setup.entry_price - setup.sl_price) / setup.entry_price * 100 if setup.entry_price else 0
                        tp1_pct = (setup.tp1_price - setup.entry_price) / setup.entry_price * 100 if setup.entry_price else 0
                        print(f"   ↳ ⚙️ SETUP: [{sym}] Buy Market = {self.fmt_price(setup.entry_price)} | Chốt Lời (TP1) = {self.fmt_price(setup.tp1_price)} (+{tp1_pct:.1f}%) | Cắt Lỗ (SL) = {self.fmt_price(setup.sl_price)} (-{sl_pct:.1f}%) | R/R = 1:{setup.rr_ratio:.1f}")
                
                if score > 0:
                    print("-" * 175)
            print("=" * 175 + "\n")
        except Exception as e:
            print(f"Render Error (Momentum Breakout): {e}")

    def render_hot_trend_pullback(self, symbol_states: List[SymbolState], htb_count: int, htb_threshold: float, tracking_list: List[str]):
        """Render Động Cơ 4: Hot Trend Pullback"""
        try:
            print("\n" + "=" * 175)
            print(f"🔥 BẢNG CHẤM ĐIỂM HOT TREND PULLBACK (ĐỘNG CƠ 4 - SĂN ĐIỂM VÀO LỆNH PULLBACK)")
            print(f"   📡 Nguồn: {htb_count} mã Hot Trend (change_24h >= {htb_threshold}%, vol >= 3M USDT) — Độc lập với watchlist tích lũy")
            print("=" * 175)
            
            if not symbol_states:
                print("⚠️  KHÔNG TÌM THẤY MÃ NÀO ĐỦ ĐIỀU KIỆN HOT TREND PULLBACK HIỆN TẠI.")
                print("    → Thị trường chưa có nhịp pullback rõ ràng, hoặc các mã tăng đang vẫn ở đỉnh.")
                print("-" * 175)
            
            for state in symbol_states:
                score_ctx = state.scores.get("DC4")
                if not score_ctx:
                    continue
                
                sym = state.symbol
                score = score_ctx.total_score
                act = score_ctx.action_label
                c1 = score_ctx.c1_score
                c2 = score_ctx.c2_score
                c3 = score_ctx.c3_score
                c4 = score_ctx.c4_score
                c5 = score_ctx.bonus_score
                
                print(f"[{sym:<6}] Điểm: {score:<12} | RSI1H: {score_ctx.rsi_1h:<5.1f} | Pull%: {score_ctx.pullback_pct:<6.1f} | 🎯 Hành Động: {act}")
                print(f"   ↳ 📈 C1 Trend  : {c1}")
                print(f"   ↳ 📉 C2 Pullbck: {c2}")
                print(f"   ↳ 📊 C3 Volume : {c3}")
                print(f"   ↳ 🧱 C4 Bệ Đỡ  : {c4}")
                print(f"   ↳ 💸 C5 Taker  : {c5}")
                
                macro = state.macro_state
                if macro:
                    print(f"   ↳ 🌀 C0 Macro  : {macro.trend_label} | {macro.drop_180d_pct}% | {macro.ma_status}")
                
                if score_ctx.entry_setup1:
                    setup = score_ctx.entry_setup1
                    sl_pct = (setup.entry_price - setup.sl_price) / setup.entry_price * 100 if setup.entry_price else 0
                    tp1_pct = (setup.tp1_price - setup.entry_price) / setup.entry_price * 100 if setup.entry_price else 0
                    print(f"   ↳ ⚙️ SETUP [{sym}] Buy={self.fmt_price(setup.entry_price)} | SL={self.fmt_price(setup.sl_price)} (-{sl_pct:.1f}%) | TP={self.fmt_price(setup.tp1_price)} (+{tp1_pct:.1f}%) | R/R=1:{setup.rr_ratio:.1f}")
                
                print("-" * 175)
                
            if tracking_list:
                print("👀 THEO DÕI THÊM: " + ", ".join(tracking_list))
                print("-" * 175)
                
            print("=" * 175 + "\n")
        except Exception as e:
            print(f"Render Error (Hot Trend Pullback): {e}")

    def render_coin_filter_results(self, filtered_lines: List[str]):
        """Render kết quả quét tổng quan (Coin Filter)"""
        try:
            if filtered_lines:
                print("\n" + "=" * 80)
                print("📊 KẾT QUẢ PHÂN TÍCH THỊ TRƯỜNG (COIN FILTER)")
                print("=" * 80)
                print("\n".join(filtered_lines))
        except Exception as e:
            print(f"Render Error (Coin Filter): {e}")

