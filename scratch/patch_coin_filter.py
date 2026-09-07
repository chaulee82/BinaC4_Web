import sys
import re

content = open('core/coin_filter.py', 'r', encoding='utf-8').read()

# Block 1: analyze_early
old_analyze = '''def analyze_early(symbol):
    info = live_data_map.get(symbol, {})'''

new_analyze = '''def analyze_early(symbol):
    return None

    info = live_data_map.get(symbol, {})'''

if old_analyze in content:
    content = content.replace(old_analyze, new_analyze)

# Block 2: BẢNG 3 UI
new_bang_3 = '''    # ── 7. 🌱 BẢNG 3 - BẮ SỜM NỀN TĂNG (in sau GRID) ───────────────────
    print(f"\\n🌱 Đang quét Bảng 3 - Móng Vĩ Mô 1D...\\n")
    
    from core.macro_early_scanner import MacroEarlyScanner
    scanner = MacroEarlyScanner()
    early_symbols = [
        s for s in live_data_map
        if s.endswith('USDT') and s not in EXCLUDE
    ]
    early_list = scanner.scan_macro_1d(early_symbols)
    
    _WCOLS3 = [12, 12, 12, 12, 12]
    _TW3    = sum(_WCOLS3) + len(_SEP) * (len(_WCOLS3) - 1)
    def fmt_row3(cells):
        return _SEP.join(ljust_w(trunc_w(c, w), w) for c, w in zip(cells, _WCOLS3))

    print("=" * _TW3)
    print("🌱 BẢNG 3: BẮT SỚM NỀN TĂNG SPOT GRID (WATCHLIST VĨ MÔ 1D)")
    print("=" * _TW3)

    if not early_list:
        print("⚠️ KHÔNG TÌM THẤY MÃ NÀO ĐẠT ĐIỀU KIỆN MÓNG VĨ MÔ.")
    else:
        print(fmt_row3(["Mã", "Giá", "Chiết Khấu", "Darvas BB", "MA99 Slope"]))
        print("-" * _TW3)
        for r in early_list:
            print(fmt_row3([
                r['symbol'].replace('USDT', ''),
                smart_price(r['current_price']),
                f"{r['drop_pct']:.1%}",
                f"{r['box']['amplitude']:.1%}",
                f"{r['ma99_slope']:.2%}"
            ]))
    
    print("-" * _TW3)
    print("🔔 LƯU Ý: Quá trình quét ngòi nổ vi mô (15M) đang chạy ngầm trong background_loop.py")
    print("   (Mỗi 3 phút cập nhật một lần để bắt Pocket Pivots và Micro Squeeze)")
    print("=" * _TW3 + "\\n")
    
    # ── 8. 🏆 BẢNG CHẤM ĐIỂM REBALANCE (in sau cùng) ──────────────────
    if summary_list:
        print("=" * _TW)'''

pattern = re.compile(r'    # ── 7\. 🌱 BẢNG 3.*?    # ── 8\. 🏆 BẢNG CHẤM ĐIỂM REBALANCE \(in sau cùng\) ──────────────────\n    if summary_list:', re.DOTALL)
if pattern.search(content):
    content = pattern.sub(new_bang_3, content)

with open('core/coin_filter.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Done!')
