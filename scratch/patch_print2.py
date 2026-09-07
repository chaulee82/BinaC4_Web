import sys

content = open('core/coin_filter.py', 'r', encoding='utf-8').read()

start1_idx = content.find('    _WCOLS3 = [8, 12, 8, 12, 12, 12, 10]')
end1_idx = content.find('print("=" * _TW3 + "\\n")', start1_idx)
if start1_idx != -1 and end1_idx != -1:
    end1_idx += len('print("=" * _TW3 + "\\n")')
    block1 = content[start1_idx:end1_idx]
else:
    print("Could not find block1")
    sys.exit(1)

start2_idx = content.find('    # ── 8. 🏆 BẢNG CHẤM ĐIỂM REBALANCE')
end2_idx = content.find('        print("\\n" + "=" * _TW + "\\n")', start2_idx)
if start2_idx != -1 and end2_idx != -1:
    end2_idx += len('        print("\\n" + "=" * _TW + "\\n")')
    block2 = content[start2_idx:end2_idx]
else:
    print("Could not find block2")
    sys.exit(1)

# Remove the blocks from get_filtered_symbols
content = content.replace(block1, "")
content = content.replace(block2, "")

# Append them at the end of the file in a new function
new_func = f"""
def print_final_tables(early_list, df_summary, current_time_str):
    if df_summary is None or df_summary.empty:
        summary_list = []
    else:
        summary_list = [1]
{block1}
{block2}
"""
content += new_func

# Also change return of get_filtered_symbols
old_ret = "    return symbols_ordered, safety_map"
new_ret = "    return symbols_ordered, safety_map, early_list, df_summary if summary_list else None, current_time_str if summary_list else \"\""
content = content.replace(old_ret, new_ret)

open('core/coin_filter.py', 'w', encoding='utf-8').write(content)

# Now patch main.py
main_content = open('main.py', 'r', encoding='utf-8').read()

old_call = "watchlist, safety_map = get_filtered_symbols(live_data_map)"
new_call = "watchlist, safety_map, early_list, df_summary, current_time_str = get_filtered_symbols(live_data_map)"
main_content = main_content.replace(old_call, new_call)

old_finish = 'logger.info("Hoàn tất quét thị trường. Chương trình kết thúc.")'
new_finish = '''from core.coin_filter import print_final_tables
            print_final_tables(early_list, df_summary, current_time_str)
            
            logger.info("Hoàn tất quét thị trường. Chương trình kết thúc.")'''
main_content = main_content.replace(old_finish, new_finish)

open('main.py', 'w', encoding='utf-8').write(main_content)

print("SUCCESS")
