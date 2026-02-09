"""
CPAP 全期間データ CSV エクスポート

全ディレクトリのデータをセッションモデルに基づき集計し、CSV に出力する。
計算ロジックは weekly_report.py を再利用。
"""

import csv
import os
import sys
from pathlib import Path

from weekly_report import compute_night_metrics, get_usetime_records

# ---------------------------------------------------------------------------
# CSV カラム定義
# ---------------------------------------------------------------------------

COLUMNS = [
    "date",
    "usage_hours",
    "ahi",
    "ai_count",
    "hi_count",
    "csa_count",
    "snore_count",
    "ai_total_duration_sec",
    "hi_total_duration_sec",
    "leak_avg_lpm",
    "pressure_min",
    "pressure_max",
    "pressure_mean",
    "pressure_median",
    "pressure_p90",
    "pressure_p95",
    "br_mean",
    "br_median",
    "tv_mean",
    "tv_median",
]


# ---------------------------------------------------------------------------
# 全期間集計
# ---------------------------------------------------------------------------

def collect_all_nights(base_dir: Path) -> list[dict]:
    all_dates = sorted(
        d for d in os.listdir(base_dir)
        if d.isdigit() and len(d) == 8
    )

    nights = []
    for i, date_str in enumerate(all_dates):
        curr_dir = str(base_dir / date_str)
        prev_dir = str(base_dir / all_dates[i - 1]) if i > 0 else None

        # usetime が存在しなければスキップ
        if not get_usetime_records(curr_dir):
            continue

        m = compute_night_metrics(date_str, prev_dir, curr_dir)
        if m is None:
            continue

        # date を YYYY-MM-DD 形式に変換
        m["date"] = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

        nights.append(m)

        if len(nights) % 100 == 0:
            print(f"  {len(nights)} 晩集計済み ... ({date_str})", flush=True)

    return nights


# ---------------------------------------------------------------------------
# CSV 出力
# ---------------------------------------------------------------------------

def write_csv(nights: list[dict], output_path: str):
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for night in nights:
            # 浮動小数点の丸め
            row = {}
            for col in COLUMNS:
                val = night.get(col, "")
                if isinstance(val, float):
                    row[col] = round(val, 2)
                else:
                    row[col] = val
            writer.writerow(row)

    print(f"\n  Saved: {output_path}")


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    base_dir = Path(__file__).parent / "DATAFILE"
    output = str(Path(__file__).parent / "cpap_all_data.csv")

    if len(sys.argv) > 1:
        output = sys.argv[1]

    print("CPAP 全期間データ集計中...")
    nights = collect_all_nights(base_dir)
    print(f"\n  合計 {len(nights)} 晩のデータを集計しました。")

    if not nights:
        print("データが見つかりませんでした。")
        sys.exit(1)

    print("\nCSV を出力中...")
    write_csv(nights, output)
    print("完了!")
