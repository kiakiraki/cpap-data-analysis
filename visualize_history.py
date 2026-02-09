"""
CPAP 全期間データ可視化

全ディレクトリの使用時間・AHI・AI件数・リークを集計し、
散布図 + 30日移動平均でトレンドを可視化する。

セッションモデル: 1晩 = dir(X-1) のバウンダリ + dir(X) の非バウンダリ
"""

import struct
import os
import sys
from datetime import datetime, date
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

HEADER_SIZE = 512
MIN_SESSION_SEC = 1500  # 25分未満のセグメントは除外


# ---------------------------------------------------------------------------
# データ読み取りユーティリティ
# ---------------------------------------------------------------------------

def get_usetime_records(day_dir: str) -> list[dict]:
    day_name = os.path.basename(day_dir)
    path = os.path.join(day_dir, f"{day_name}_usetime.edf")
    if not os.path.exists(path):
        return []
    with open(path, "rb") as f:
        data = f.read()
    raw = data[HEADER_SIZE:]
    n = len(raw) // 16
    records = []
    for i in range(n):
        off = i * 16
        val = struct.unpack_from("<I", raw, off)[0]
        minute = raw[off + 13]
        second = raw[off + 14]
        records.append({"val": val, "boundary": (minute == 0 and second == 0)})
    return records


def compute_night_hours(prev_dir: str | None, curr_dir: str) -> float:
    evening_sec = 0
    if prev_dir:
        prev_recs = get_usetime_records(prev_dir)
        boundary = [r for r in prev_recs if r["boundary"]]
        if boundary:
            evening_sec = boundary[-1]["val"]

    curr_recs = get_usetime_records(curr_dir)
    has_boundary = any(r["boundary"] for r in curr_recs)
    non_boundary = [r for r in curr_recs if not r["boundary"]]

    if has_boundary:
        morning_sec = sum(
            r["val"] for r in non_boundary if r["val"] >= MIN_SESSION_SEC
        )
    else:
        # バウンダリなし: 最大のレコードを使用
        # (初期データは全セッションが1ディレクトリ内、最大値が本セッション)
        morning_sec = max((r["val"] for r in non_boundary), default=0)

    return (evening_sec + morning_sec) / 3600.0


def count_events(day_dir: str, suffix: str) -> int:
    day_name = os.path.basename(day_dir)
    path = os.path.join(day_dir, f"{day_name}_{suffix}.edf")
    if not os.path.exists(path):
        return 0
    return max(0, (os.path.getsize(path) - HEADER_SIZE) // 16)


def compute_leak_avg(prev_dir: str | None, curr_dir: str) -> float:
    """夕方(prev_dir) + 朝(curr_dir) の difleak 加重平均 (L/min)"""

    def read_difleak(day_dir):
        day_name = os.path.basename(day_dir)
        path = os.path.join(day_dir, f"{day_name}_difleak.edf")
        if not os.path.exists(path):
            return bytes(), [], 0
        with open(path, "rb") as f:
            data = f.read()
        raw = data[HEADER_SIZE:]
        recs = get_usetime_records(day_dir)
        ut_sum = sum(r["val"] for r in recs)
        return raw, recs, ut_sum

    leak_samples = bytearray()

    # 夕方分 (prev_dir の末尾)
    if prev_dir:
        raw, recs, ut_sum = read_difleak(prev_dir)
        boundary = [r for r in recs if r["boundary"]]
        if boundary and ut_sum > 0 and len(raw) > 0:
            ratio = boundary[-1]["val"] / ut_sum
            n = int(ratio * len(raw))
            if n > 0:
                leak_samples.extend(raw[-n:])

    # 朝分 (curr_dir の先頭)
    raw, recs, ut_sum = read_difleak(curr_dir)
    non_boundary_sum = sum(r["val"] for r in recs if not r["boundary"])
    if non_boundary_sum > 0 and ut_sum > 0 and len(raw) > 0:
        ratio = non_boundary_sum / ut_sum
        n = int(ratio * len(raw))
        if n > 0:
            leak_samples.extend(raw[:n])

    if len(leak_samples) == 0:
        return 0.0
    return sum(leak_samples) / len(leak_samples)


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
        curr_recs = get_usetime_records(curr_dir)
        if not curr_recs:
            continue

        hours = compute_night_hours(prev_dir, curr_dir)
        if hours <= 0:
            continue

        ai = count_events(curr_dir, "ai")
        hi = count_events(curr_dir, "hi")
        ahi = (ai + hi) / hours if hours > 0 else 0

        leak = compute_leak_avg(prev_dir, curr_dir)

        try:
            dt = date(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]))
        except ValueError:
            continue

        nights.append({
            "date": dt,
            "hours": hours,
            "ahi": ahi,
            "ai": ai,
            "leak": leak,
        })

        # 進捗表示 (100日ごと)
        if len(nights) % 100 == 0:
            print(f"  {len(nights)} 晩集計済み ... ({date_str})", flush=True)

    return nights


# ---------------------------------------------------------------------------
# 移動平均
# ---------------------------------------------------------------------------

def rolling_mean(values: np.ndarray, window: int) -> np.ndarray:
    kernel = np.ones(window) / window
    # 端はNaNで埋める
    result = np.convolve(values, kernel, mode="same")
    half = window // 2
    result[:half] = np.nan
    result[-half:] = np.nan
    return result


# ---------------------------------------------------------------------------
# 可視化
# ---------------------------------------------------------------------------

def plot_history(nights: list[dict], output_path: str):
    dates = np.array([n["date"] for n in nights])
    hours = np.array([n["hours"] for n in nights])
    ahi = np.array([n["ahi"] for n in nights])
    ai = np.array([n["ai"] for n in nights])
    leak = np.array([n["leak"] for n in nights])

    window = 30

    hours_ma = rolling_mean(hours, window)
    ahi_ma = rolling_mean(ahi, window)
    ai_ma = rolling_mean(ai, window)
    leak_ma = rolling_mean(leak, window)

    fig, axes = plt.subplots(4, 1, figsize=(18, 16), sharex=False)
    fig.suptitle(
        "CPAP Long-term Trends  —  Hypnus CA820M",
        fontsize=14, fontweight="bold", y=0.98,
    )

    dot_kw = dict(s=4, alpha=0.25, edgecolors="none")
    line_kw = dict(linewidth=1.8)
    ma_label = f"{window}-day MA"

    # ---- 1. 使用時間 ----
    ax = axes[0]
    ax.scatter(dates, hours, color="#4A90D9", label="Daily", **dot_kw)
    ax.plot(dates, hours_ma, color="#1A4E8A", label=ma_label, **line_kw)
    ax.axhline(y=4, color="#E74C3C", linewidth=1, linestyle="--",
               alpha=0.7, label="Compliance (4h)")
    ax.set_ylabel("Usage (hours)")
    ax.set_ylim(bottom=0)
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(True, alpha=0.3)

    # ---- 2. AHI ----
    ax = axes[1]
    ax.scatter(dates, ahi, color="#27AE60", label="Daily", **dot_kw)
    ax.plot(dates, ahi_ma, color="#1B7A3D", label=ma_label, **line_kw)
    # 重症度バンド
    ax.axhspan(0, 5, color="#27AE60", alpha=0.06)
    ax.axhspan(5, 15, color="#F39C12", alpha=0.06)
    ax.axhspan(15, 30, color="#E67E22", alpha=0.06)
    ax.axhline(y=5, color="#F39C12", linewidth=0.8, linestyle="--", alpha=0.6)
    ax.axhline(y=15, color="#E67E22", linewidth=0.8, linestyle="--", alpha=0.6)
    ax.axhline(y=30, color="#E74C3C", linewidth=0.8, linestyle="--", alpha=0.6)
    # ラベル
    ax.text(dates[-1], 2.5, " Normal", fontsize=7, color="#27AE60",
            va="center", ha="left")
    ax.text(dates[-1], 10, " Mild", fontsize=7, color="#F39C12",
            va="center", ha="left")
    ax.text(dates[-1], 22, " Moderate", fontsize=7, color="#E67E22",
            va="center", ha="left")
    ax.set_ylabel("AHI (events/h)")
    ax.set_ylim(bottom=0, top=max(35, np.nanpercentile(ahi, 99.5) * 1.2))
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(True, alpha=0.3)

    # ---- 3. AI 件数 ----
    ax = axes[2]
    ax.scatter(dates, ai, color="#8E44AD", label="Daily", **dot_kw)
    ax.plot(dates, ai_ma, color="#5B2C7A", label=ma_label, **line_kw)
    ax.set_ylabel("Apnea events (count)")
    ax.set_ylim(bottom=0)
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(True, alpha=0.3)

    # ---- 4. リーク ----
    ax = axes[3]
    ax.scatter(dates, leak, color="#E67E22", label="Daily", **dot_kw)
    ax.plot(dates, leak_ma, color="#A0522D", label=ma_label, **line_kw)
    ax.set_ylabel("Leak (L/min)")
    ax.set_ylim(bottom=0)
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(True, alpha=0.3)

    # X軸フォーマット — 全パネルに日付を表示
    for ax in axes:
        ax.set_xlim(dates[0], dates[-1])
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax.xaxis.set_minor_locator(mdates.MonthLocator())
        ax.tick_params(axis="x", labelsize=10, rotation=30)
        plt.setp(ax.xaxis.get_majorticklabels(), ha="right")

    axes[-1].set_xlabel("Date", fontsize=11)

    # 期間情報
    n_days = len(nights)
    span = (dates[-1] - dates[0]).days
    fig.text(
        0.5, 0.005,
        f"Total: {n_days} nights over {span} days "
        f"({dates[0].strftime('%Y-%m-%d')} — {dates[-1].strftime('%Y-%m-%d')})",
        ha="center", fontsize=10, color="#666",
    )

    plt.tight_layout(rect=[0, 0.03, 1, 0.96])
    fig.subplots_adjust(hspace=0.35)
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"\n  Saved: {output_path}")
    plt.close()


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    base_dir = Path(__file__).parent / "DATAFILE"
    output = str(Path(__file__).parent / "cpap_history.png")

    if len(sys.argv) > 1:
        output = sys.argv[1]

    print("CPAP 全期間データ集計中...")
    nights = collect_all_nights(base_dir)
    print(f"\n  合計 {len(nights)} 晩のデータを集計しました。")

    if not nights:
        print("データが見つかりませんでした。")
        sys.exit(1)

    print("\nグラフを生成中...")
    plot_history(nights, output)
    print("完了!")
