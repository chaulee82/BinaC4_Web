import re

content = open('core/coin_filter.py', 'r', encoding='utf-8').read()

# 1. Capture Bảng 3 printing
bang3_start = r'    _WCOLS3 = \[8, 12, 8, 12, 12, 12, 10\].*?    print\("=" \* _TW3 \+ "\\n"\)'

# 2. Capture Rebalance printing
rebalance_start = r'    # ── 8\. 🏆 BẢNG CHẤM ĐIỂM REBALANCE \(in sau cùng\) ──────────────────\n    if summary_list:.*?        print\("\\n" \+ "=" \* _TW \+ "\\n"\)'

m1 = re.search(bang3_start, content, re.DOTALL)
m2 = re.search(rebalance_start, content, re.DOTALL)

print("Found Bảng 3 pattern:", bool(m1))
print("Found Rebalance pattern:", bool(m2))

if m1 and m2:
    bang3_code = m1.group(0)
    rebalance_code = m2.group(0)

    # Remove them from get_filtered_symbols
    content = content.replace(bang3_code, "")
    content = content.replace(rebalance_code, "")

    # Create the new function at the end of the file
    new_func = f"""
def print_final_tables(early_list, df_summary, current_time_str):
    # Needed for fmt_row3 and fmt_row
{bang3_code.replace('    _WCOLS3', '    _WCOLS3')}

{rebalance_code}
"""
    # Fix the indentation of new_func
    # Actually, bang3_code has 4 spaces indent. new_func will just have them inside def.
    # Wait, we also need _TW, _SEP, fmt_row, smart_price, ljust_w, trunc_w
    # Those are defined inside or outside?
    # smart_price, ljust_w, trunc_w, fmt_row are module level.
    # _TW, _SEP are module level.
    pass
