import re
import os
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
from datetime import datetime

def test_parse(file_path):
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    # Find log date
    date_str = None
    for line in lines[:150]:
        m = re.search(r"(\d{4}-\d{2}-\d{2})\s+\d{2}:\d{2}:\d{2}", line)
        if m:
            date_str = m.group(1)
            break
    if not date_str:
        date_str = datetime.fromtimestamp(os.path.getmtime(file_path)).strftime('%Y-%m-%d')

    print(f"File: {file_path} -> Log Date: {date_str}")

    results = {}
    current_symbol = None

    re_strict = re.compile(r"\[3D STRICT[^\]]*\]\s*(\d+)", re.IGNORECASE)
    re_table_sym = re.compile(r"^([A-Z0-9]{2,12})(?:/USDT|USDT)?\s*\|\s*[\d\.\+\-]+", re.IGNORECASE)
    re_grid_sym = re.compile(r"\[(?:GRID TP2|WIDE GRID 3D|DUAL|GRID)\s*[-–—:]\s*([A-Z0-9]+)\]", re.IGNORECASE)
    re_summary_line = re.compile(r"#\d+\s+([A-Z0-9]{2,12})\s*\|\s*([A-Z0-9]+)\s*\|.*\[3D STRICT[^\]]*\]\s*(\d+)", re.IGNORECASE)

    ignore_words = {"MỨC", "LEVEL", "TÍN", "DANH", "MÃ", "SYMBOL", "TOTAL", "PRICE", "ACTION", "SCORE", "LOW", "UP"}

    def clean(s):
        if not s:
            return ""
        s = s.upper().strip()
        if s.endswith("/USDT"):
            s = s[:-5]
        elif s.endswith("USDT") and len(s) > 4:
            s = s[:-4]
        return s

    for idx, line in enumerate(lines):
        clean_line = line.strip()

        # Track symbol from table header like "FIL | 85 | ✅ AN TOÀN" or "SKLUSDT | 40.8912 | ..."
        m_sym = re_table_sym.match(clean_line)
        if m_sym:
            cand = clean(m_sym.group(1))
            if cand not in ignore_words and len(cand) >= 2:
                current_symbol = cand

        # Pattern 1: Summary line like "#1 RAY | DC2 | 🛡️ [3D STRICT ✅] 100đ | 85đ"
        m_sum = re_summary_line.search(clean_line)
        if m_sum:
            sym = clean(m_sum.group(1))
            score = float(m_sum.group(3))
            results[sym] = max(results.get(sym, 0.0), score)
            continue

        # Pattern 2: [3D STRICT] on the line
        m_strict = re_strict.search(clean_line)
        if m_strict:
            strict_score = float(m_strict.group(1))
            sym = None
            m_g = re_grid_sym.search(clean_line)
            if m_g:
                sym = clean(m_g.group(1))
            elif current_symbol:
                sym = current_symbol
            else:
                for back_idx in range(max(0, idx - 4), idx):
                    b_line = lines[back_idx].strip()
                    m_b = re_table_sym.match(b_line)
                    if m_b:
                        cand = clean(m_b.group(1))
                        if cand not in ignore_words:
                            sym = cand
                            break

            if sym and sym not in {"USDT", "GRID", "WIDE", "DUAL", "LEVEL", "SCORE"}:
                results[sym] = max(results.get(sym, 0.0), strict_score)

    print(f"Total symbols with [3D STRICT] found: {len(results)}")
    for s, sc in sorted(results.items()):
        print(f"  {s:10}: {sc}đ")

if __name__ == "__main__":
    test_parse("main_run_3b_utf8.txt")
