import textwrap, pathlib
code = textwrap.dedent('''
    import pandas as pd

    def apply_anti_pump_filter(klines_1d_df, current_price):
        \"\"\"
        Phat hien hanh vi bom/xa bat thuong va cham diem Danger Score.
        Args:
            klines_1d_df: DataFrame nen 1D co cot OHLC (viet hoa hoac thuong).
            current_price: Gia realtime hien tai.
        Returns:
            dict: is_safe, danger_score, dump_pct, reason
        \"\"\"
        try:
            if klines_1d_df is None or len(klines_1d_df) < 14:
                return {"is_safe": False, "danger_score": 0.0, "dump_pct": 0.0, "reason": "Thieu du lieu 1D (< 14 nen)"}

            df = klines_1d_df.copy()
            col_map = {}
            for target in ["high", "low", "open", "close"]:
                for col in df.columns:
                    if col.lower() == target:
                        col_map[target] = col
                        break
            if len(col_map) < 4:
                return {"is_safe": False, "danger_score": 0.0, "dump_pct": 0.0, "reason": "DataFrame thieu cot OHLC"}

            df = df.rename(columns={v: k for k, v in col_map.items()})
            for col in ["high", "low", "open", "close"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")

            recent_14 = df.tail(14).copy()
            recent_14["tr0"] = abs(recent_14["high"] - recent_14["low"])
            recent_14["tr1"] = abs(recent_14["high"] - recent_14["close"].shift(1))
            recent_14["tr2"] = abs(recent_14["low"]  - recent_14["close"].shift(1))
            recent_14["tr"]  = recent_14[["tr0", "tr1", "tr2"]].max(axis=1)
            atr_14 = recent_14["tr"].mean()

            if atr_14 <= 0:
                return {"is_safe": True, "danger_score": 0.0, "dump_pct": 0.0, "reason": "ATR=0, bo qua"}

            recent_5   = recent_14.tail(5)
            peak_price = recent_5["high"].max()
            dump_ratio = (peak_price - current_price) / peak_price if peak_price > 0 else 0.0

            danger_score = 0.0
            reasons      = []

            # Tin hieu 1: Nen Climax / Kim Tiem Bom Gia
            for _, row in recent_5.iterrows():
                body       = abs(row["close"] - row["open"])
                upper_wick = row["high"] - max(row["open"], row["close"])

                # Climax: Than nen > 3x ATR
                if body > (3.0 * atr_14):
                    ratio = body / atr_14
                    reasons.append(f"Climax {ratio:.1f}x ATR")
                    danger_score += ratio * 10.0

                # Rau xa: Rau tren > 2x Than (Smart Money xa dinh)
                if body > 0 and upper_wick > (2.0 * body):
                    ratio = upper_wick / body
                    reasons.append(f"Rau xa {ratio:.1f}x Than")
                    danger_score += ratio * 5.0

            # Tin hieu 2: Dump Ratio - Gia rot sau tu dinh 5 ngay
            if dump_ratio > 0.15:
                reasons.append(f"Xa -{dump_ratio:.1%} tu dinh")
                danger_score += dump_ratio * 100.0

            danger_score = round(danger_score, 1)

            if reasons:
                return {
                    "is_safe":      False,
                    "danger_score": danger_score,
                    "dump_pct":     round(dump_ratio * 100.0, 1),
                    "reason":       " | ".join(reasons)
                }

            return {"is_safe": True, "danger_score": 0.0, "dump_pct": round(dump_ratio * 100.0, 1), "reason": "An toan"}

        except Exception as e:
            return {"is_safe": False, "danger_score": 0.0, "dump_pct": 0.0, "reason": f"Loi filter: {str(e)}"}
''').lstrip()

pathlib.Path(r'C:/DData/Source/wwwScr/BinaC4/core/anti_pump_filter.py').write_text(code, encoding='utf-8')
print('DONE')
