"""
CPAP 週間診断レポート

直近 N 日分のデータを集計し、臨床で一般的に使用される指標を出力する。

重要: 1晩のセッションは2つのディレクトリにまたがる。
  - 夕方セッション: ディレクトリ dir(X-1) の後半部分
  - 朝セッション:   ディレクトリ dir(X) の前半部分
  夜 "X" = dir(X-1) のバウンダリレコード + dir(X) の非バウンダリレコード
"""

import struct
import os
import sys
from datetime import datetime
from pathlib import Path
from statistics import median


# ---------------------------------------------------------------------------
# パーサー
# ---------------------------------------------------------------------------

HEADER_SIZE = 512


def _read(filepath: str) -> bytes:
    with open(filepath, "rb") as f:
        return f.read()


def percentile(sorted_list: list, p: float) -> float:
    """ソート済みリストから p パーセンタイルを返す (0-100)"""
    if not sorted_list:
        return 0.0
    k = (len(sorted_list) - 1) * p / 100.0
    f = int(k)
    c = f + 1
    if c >= len(sorted_list):
        return float(sorted_list[f])
    return sorted_list[f] + (k - f) * (sorted_list[c] - sorted_list[f])


# ---------------------------------------------------------------------------
# usetime レコード解析
# ---------------------------------------------------------------------------

def get_usetime_records(day_dir: str) -> list[dict]:
    """usetime.edf からレコードを読み取る。
    各レコード: {val: 秒数, boundary: bool (タイムスタンプが XX:00:00 のとき True)}
    """
    day_name = os.path.basename(day_dir)
    path = os.path.join(day_dir, f"{day_name}_usetime.edf")
    if not os.path.exists(path):
        return []

    data = _read(path)
    raw = data[HEADER_SIZE:]
    n = len(raw) // 16
    records = []
    for i in range(n):
        off = i * 16
        val = struct.unpack_from("<I", raw, off)[0]
        minute = raw[off + 13]
        second = raw[off + 14]
        is_boundary = (minute == 0 and second == 0)
        records.append({"val": val, "boundary": is_boundary})
    return records


MIN_SESSION_SEC = 1500  # 25分未満のセッションは別セッション扱い


def compute_night_hours(prev_dir: str | None, curr_dir: str) -> float:
    """1晩の使用時間を計算する。
    夜 X = dir(X-1) の最後のバウンダリレコード + dir(X) の非バウンダリレコード
    """
    evening_sec = 0
    if prev_dir:
        prev_recs = get_usetime_records(prev_dir)
        boundary_recs = [r for r in prev_recs if r["boundary"]]
        if boundary_recs:
            evening_sec = boundary_recs[-1]["val"]

    curr_recs = get_usetime_records(curr_dir)
    has_boundary = any(r["boundary"] for r in curr_recs)
    non_boundary = [r for r in curr_recs if not r["boundary"]]

    if has_boundary:
        # バウンダリあり: 短すぎるセグメントは除外 (独立した短時間セッション)
        morning_sec = sum(
            r["val"] for r in non_boundary
            if r["val"] >= MIN_SESSION_SEC
        )
    else:
        # バウンダリなし: 最大のレコードを使用
        # (初期データは全セッションが1ディレクトリ内、最大値が本セッション)
        morning_sec = max((r["val"] for r in non_boundary), default=0)

    total_sec = evening_sec + morning_sec
    return total_sec / 3600.0


# ---------------------------------------------------------------------------
# ディレクトリレベルのデータ抽出 (イベント・圧力・呼吸・リーク)
# ---------------------------------------------------------------------------

def extract_dir_data(day_dir: str) -> dict:
    """1ディレクトリ分の生データを抽出する。"""
    day_name = os.path.basename(day_dir)

    def count_events(suffix: str) -> int:
        p = os.path.join(day_dir, f"{day_name}_{suffix}.edf")
        if not os.path.exists(p):
            return 0
        return max(0, (os.path.getsize(p) - HEADER_SIZE) // 16)

    def sum_event_durations(suffix: str) -> int:
        p = os.path.join(day_dir, f"{day_name}_{suffix}.edf")
        if not os.path.exists(p):
            return 0
        d = _read(p)
        raw = d[HEADER_SIZE:]
        n = len(raw) // 16
        total = 0
        for i in range(n):
            total += struct.unpack_from("<I", raw, i * 16 + 4)[0]
        return total

    # 圧力 (APCP)
    apcp_path = os.path.join(day_dir, f"{day_name}_apcp.edf")
    pressure_values = []
    if os.path.exists(apcp_path):
        apcp_data = _read(apcp_path)
        raw = apcp_data[HEADER_SIZE:]
        n = len(raw) // 16
        for i in range(n):
            p = struct.unpack_from("<I", raw, i * 16)[0]
            pressure_values.append(p / 10.0)

    # 呼吸指標 (mvtvbr)
    mvt_path = os.path.join(day_dir, f"{day_name}_mvtvbr.edf")
    tv_values = []
    br_values = []
    if os.path.exists(mvt_path):
        mvt_data = _read(mvt_path)
        raw = mvt_data[HEADER_SIZE:]
        n = len(raw) // 6
        for i in range(n):
            off = i * 6
            tv = struct.unpack_from("<H", raw, off)[0]
            br = struct.unpack_from("<H", raw, off + 2)[0]
            if br > 0:
                tv_values.append(tv)
                br_values.append(br)

    # difleak (1バイト/サンプル, 2.5秒間隔)
    difleak_path = os.path.join(day_dir, f"{day_name}_difleak.edf")
    difleak_raw = bytes()
    if os.path.exists(difleak_path):
        difleak_data = _read(difleak_path)
        difleak_raw = difleak_data[HEADER_SIZE:]

    # usetime (按分用)
    usetime_recs = get_usetime_records(day_dir)
    usetime_sum = sum(r["val"] for r in usetime_recs)

    return {
        "ai_count": count_events("ai"),
        "hi_count": count_events("hi"),
        "csa_count": count_events("csa"),
        "snore_count": count_events("snore"),
        "ai_duration": sum_event_durations("ai"),
        "hi_duration": sum_event_durations("hi"),
        "pressure_values": pressure_values,
        "tv_values": tv_values,
        "br_values": br_values,
        "difleak_raw": difleak_raw,
        "usetime_recs": usetime_recs,
        "usetime_sum": usetime_sum,
    }


def get_evening_difleak(dir_data: dict) -> bytes:
    """ディレクトリデータから夕方セッション分の difleak を切り出す。"""
    recs = dir_data["usetime_recs"]
    boundary_recs = [r for r in recs if r["boundary"]]
    if not boundary_recs or dir_data["usetime_sum"] == 0:
        return bytes()

    evening_ratio = boundary_recs[-1]["val"] / dir_data["usetime_sum"]
    n_samples = int(evening_ratio * len(dir_data["difleak_raw"]))
    return dir_data["difleak_raw"][-n_samples:] if n_samples > 0 else bytes()


def get_morning_difleak(dir_data: dict) -> bytes:
    """ディレクトリデータから朝セッション分の difleak を切り出す。"""
    recs = dir_data["usetime_recs"]
    non_boundary_sum = sum(r["val"] for r in recs if not r["boundary"])
    if non_boundary_sum == 0 or dir_data["usetime_sum"] == 0:
        return bytes()

    morning_ratio = non_boundary_sum / dir_data["usetime_sum"]
    n_samples = int(morning_ratio * len(dir_data["difleak_raw"]))
    return dir_data["difleak_raw"][:n_samples] if n_samples > 0 else bytes()


def get_morning_breathing(dir_data: dict) -> tuple[list, list]:
    """ディレクトリデータから朝セッション分の呼吸指標を切り出す。"""
    recs = dir_data["usetime_recs"]
    non_boundary_sum = sum(r["val"] for r in recs if not r["boundary"])
    if non_boundary_sum == 0 or dir_data["usetime_sum"] == 0:
        return [], []

    morning_ratio = non_boundary_sum / dir_data["usetime_sum"]
    n_tv = int(morning_ratio * len(dir_data["tv_values"]))
    n_br = int(morning_ratio * len(dir_data["br_values"]))
    return dir_data["tv_values"][:n_tv], dir_data["br_values"][:n_br]


def get_evening_breathing(dir_data: dict) -> tuple[list, list]:
    """ディレクトリデータから夕方セッション分の呼吸指標を切り出す。"""
    recs = dir_data["usetime_recs"]
    boundary_recs = [r for r in recs if r["boundary"]]
    if not boundary_recs or dir_data["usetime_sum"] == 0:
        return [], []

    evening_ratio = boundary_recs[-1]["val"] / dir_data["usetime_sum"]
    n_tv = int(evening_ratio * len(dir_data["tv_values"]))
    n_br = int(evening_ratio * len(dir_data["br_values"]))
    return dir_data["tv_values"][-n_tv:], dir_data["br_values"][-n_br:]


# ---------------------------------------------------------------------------
# 1晩分の診断指標を集計
# ---------------------------------------------------------------------------

def compute_night_metrics(
    night_date: str,
    prev_dir: str | None,
    curr_dir: str,
) -> dict | None:
    """1晩分 (dir(X-1) の夕方 + dir(X) の朝) の診断指標を計算する。"""

    curr_data = extract_dir_data(curr_dir)
    prev_data = extract_dir_data(prev_dir) if prev_dir else None

    # 使用時間
    usage_hours = compute_night_hours(prev_dir, curr_dir)
    if usage_hours <= 0:
        return None

    # イベント (現ディレクトリから取得)
    ai_count = curr_data["ai_count"]
    hi_count = curr_data["hi_count"]
    csa_count = curr_data["csa_count"]
    snore_count = curr_data["snore_count"]
    ai_duration = curr_data["ai_duration"]
    hi_duration = curr_data["hi_duration"]

    ahi = (ai_count + hi_count) / usage_hours if usage_hours > 0 else 0

    # 圧力: 両ディレクトリを結合
    pressure_values = list(curr_data["pressure_values"])
    if prev_data:
        pressure_values = list(prev_data["pressure_values"]) + pressure_values
    pressure_sorted = sorted(pressure_values)

    # 呼吸: 夕方(prev_dir) + 朝(curr_dir) を按分結合
    tv_combined = []
    br_combined = []
    if prev_data:
        ev_tv, ev_br = get_evening_breathing(prev_data)
        tv_combined.extend(ev_tv)
        br_combined.extend(ev_br)
    mr_tv, mr_br = get_morning_breathing(curr_data)
    tv_combined.extend(mr_tv)
    br_combined.extend(mr_br)

    # リーク: 夕方(prev_dir) + 朝(curr_dir) の difleak 加重平均
    leak_data = bytes()
    if prev_data:
        leak_data += get_evening_difleak(prev_data)
    leak_data += get_morning_difleak(curr_data)
    leak_avg = sum(leak_data) / len(leak_data) if len(leak_data) > 0 else 0.0

    return {
        "date": night_date,
        "usage_hours": usage_hours,
        "ai_count": ai_count,
        "hi_count": hi_count,
        "ahi": ahi,
        "csa_count": csa_count,
        "snore_count": snore_count,
        "ai_total_duration_sec": ai_duration,
        "hi_total_duration_sec": hi_duration,
        "pressure_min": min(pressure_values) if pressure_values else 0,
        "pressure_max": max(pressure_values) if pressure_values else 0,
        "pressure_mean": (
            sum(pressure_values) / len(pressure_values)
            if pressure_values
            else 0
        ),
        "pressure_median": median(pressure_values) if pressure_values else 0,
        "pressure_p90": percentile(pressure_sorted, 90),
        "pressure_p95": percentile(pressure_sorted, 95),
        "br_mean": (
            sum(br_combined) / len(br_combined) if br_combined else 0
        ),
        "br_median": median(br_combined) if br_combined else 0,
        "tv_mean": (
            sum(tv_combined) / len(tv_combined) if tv_combined else 0
        ),
        "tv_median": median(tv_combined) if tv_combined else 0,
        "leak_avg_lpm": leak_avg,
    }


# ---------------------------------------------------------------------------
# レポート出力
# ---------------------------------------------------------------------------

def format_date(d: str) -> str:
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}"


def severity_label(ahi: float) -> str:
    if ahi < 5:
        return "正常"
    elif ahi < 15:
        return "軽症"
    elif ahi < 30:
        return "中等症"
    else:
        return "重症"


def print_weekly_report(metrics_list: list[dict]):
    print()
    print("=" * 78)
    print("  CPAP 週間診断レポート")
    print("  装置: Hypnus CA820M (APAP)")
    print(f"  期間: {format_date(metrics_list[0]['date'])} "
          f"～ {format_date(metrics_list[-1]['date'])}")
    print("=" * 78)

    # -------------------------------------------------------------------
    # 日別サマリーテーブル
    # -------------------------------------------------------------------
    print()
    print("─" * 78)
    print(f"  {'日付':>10s}  {'使用時間':>7s}  {'AHI':>5s}  {'AI':>3s}  "
          f"{'HI':>3s}  {'CSA':>3s}  {'いびき':>4s}  {'リーク':>6s}  "
          f"{'圧力90%':>7s}")
    print(f"  {'':>10s}  {'(時間)':>7s}  {'(/h)':>5s}  {'(件)':>3s}  "
          f"{'(件)':>3s}  {'(件)':>3s}  {'(件)':>4s}  {'(L/min)':>6s}  "
          f"{'(cmH2O)':>7s}")
    print("─" * 78)

    total_usage = 0.0
    total_ai = 0
    total_hi = 0
    total_csa = 0
    total_snore = 0
    all_pressures_p90 = []
    all_br = []

    for m in metrics_list:
        date_str = format_date(m["date"])
        usage_str = f"{m['usage_hours']:.1f}"
        ahi_str = f"{m['ahi']:.1f}"
        leak_str = f"{m['leak_avg_lpm']:.1f}"
        p90_str = (
            f"{m['pressure_p90']:.1f}" if m["pressure_p90"] > 0 else "—"
        )

        print(f"  {date_str:>10s}  {usage_str:>7s}  {ahi_str:>5s}  "
              f"{m['ai_count']:>3d}  {m['hi_count']:>3d}  "
              f"{m['csa_count']:>3d}  {m['snore_count']:>4d}  "
              f"{leak_str:>6s}  {p90_str:>7s}")

        total_usage += m["usage_hours"]
        total_ai += m["ai_count"]
        total_hi += m["hi_count"]
        total_csa += m["csa_count"]
        total_snore += m["snore_count"]
        if m["pressure_p90"] > 0:
            all_pressures_p90.append(m["pressure_p90"])
        if m["br_mean"] > 0:
            all_br.append(m["br_mean"])

    print("─" * 78)

    # -------------------------------------------------------------------
    # 週間集計
    # -------------------------------------------------------------------
    n_days = len(metrics_list)
    avg_usage = total_usage / n_days
    overall_ahi = (
        (total_ai + total_hi) / total_usage if total_usage > 0 else 0
    )
    avg_p90 = (
        sum(all_pressures_p90) / len(all_pressures_p90)
        if all_pressures_p90
        else 0
    )
    avg_br = sum(all_br) / len(all_br) if all_br else 0

    print()
    print("  [週間サマリー]")
    print()
    print(f"  総使用日数        : {n_days} 日")
    print(f"  平均使用時間      : {avg_usage:.1f} 時間/日")
    print(f"  総使用時間        : {total_usage:.1f} 時間")
    compliance = sum(1 for m in metrics_list if m["usage_hours"] >= 4.0)
    print(f"  コンプライアンス  : {compliance}/{n_days} 日 "
          f"(4時間以上使用)")
    print()

    print("  ── 呼吸イベント ──")
    print(f"  週間 AHI          : {overall_ahi:.1f} 回/時 "
          f"→ {severity_label(overall_ahi)}")
    print(f"    無呼吸 (AI)     : 合計 {total_ai} 件 "
          f"({total_ai/n_days:.1f} 件/日)")
    print(f"    低呼吸 (HI)     : 合計 {total_hi} 件 "
          f"({total_hi/n_days:.1f} 件/日)")
    print(f"    中枢性 (CSA)    : 合計 {total_csa} 件 "
          f"({total_csa/n_days:.1f} 件/日)")
    print(f"    いびき          : 合計 {total_snore} 件 "
          f"({total_snore/n_days:.1f} 件/日)")
    print()

    print("  ── 圧力統計 ──")
    all_pmin = [m["pressure_min"] for m in metrics_list
                if m["pressure_min"] > 0]
    all_pmax = [m["pressure_max"] for m in metrics_list
                if m["pressure_max"] > 0]
    all_pmean = [m["pressure_mean"] for m in metrics_list
                 if m["pressure_mean"] > 0]
    if all_pmin:
        print(f"  最低圧力          : {min(all_pmin):.1f} cmH2O")
    if all_pmax:
        print(f"  最高圧力          : {max(all_pmax):.1f} cmH2O")
    if all_pmean:
        print(f"  平均圧力          : "
              f"{sum(all_pmean)/len(all_pmean):.1f} cmH2O")
    print(f"  90%ile 圧力 (平均): {avg_p90:.1f} cmH2O")
    print()

    print("  ── 呼吸パターン ──")
    print(f"  平均呼吸数        : {avg_br:.1f} 回/分")
    all_tv = [m["tv_mean"] for m in metrics_list if m["tv_mean"] > 0]
    if all_tv:
        print(f"  平均一回換気量    : "
              f"{sum(all_tv)/len(all_tv):.0f} mL")
    print()

    # -------------------------------------------------------------------
    # AHI 重症度の目安
    # -------------------------------------------------------------------
    print("  ── AHI 重症度基準（参考） ──")
    print("    < 5  回/時 : 正常")
    print("    5-14 回/時 : 軽症")
    print("   15-29 回/時 : 中等症")
    print("   30+   回/時 : 重症")
    print()

    # -------------------------------------------------------------------
    # 日別詳細
    # -------------------------------------------------------------------
    print("=" * 78)
    print("  日別詳細")
    print("=" * 78)

    for m in metrics_list:
        print()
        print(f"  ■ {format_date(m['date'])}")
        print(f"    使用時間: {m['usage_hours']:.1f} 時間")
        print(f"    AHI: {m['ahi']:.1f} 回/時 "
              f"({severity_label(m['ahi'])})")
        print(f"      AI={m['ai_count']}件 "
              f"(計{m['ai_total_duration_sec']}秒) "
              f"| HI={m['hi_count']}件 "
              f"(計{m['hi_total_duration_sec']}秒) "
              f"| CSA={m['csa_count']}件 "
              f"| いびき={m['snore_count']}件")
        if m["pressure_mean"] > 0:
            print(f"    圧力: {m['pressure_min']:.1f}-"
                  f"{m['pressure_max']:.1f} cmH2O "
                  f"(平均{m['pressure_mean']:.1f}, "
                  f"中央値{m['pressure_median']:.1f}, "
                  f"90%={m['pressure_p90']:.1f}, "
                  f"95%={m['pressure_p95']:.1f})")
        if m["br_mean"] > 0:
            print(f"    呼吸: {m['br_mean']:.1f} 回/分 "
                  f"(中央値{m['br_median']:.0f}), "
                  f"一回換気量 {m['tv_mean']:.0f} mL "
                  f"(中央値{m['tv_median']:.0f})")
        print(f"    リーク: {m['leak_avg_lpm']:.1f} L/min")

    print()
    print("=" * 78)
    print()
    print("  ※ イベント数はSDカード記録に基づきます。")
    print("    機械ディスプレイの値と多少異なる場合があります。")
    print()


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    base_dir = Path(__file__).parent / "DATAFILE"
    n_days = int(sys.argv[1]) if len(sys.argv) > 1 else 7

    # 利用可能な日付を降順ソートして最新 N+1 日を取得
    # (N晩分のデータには N+1 ディレクトリが必要)
    all_dates = sorted(
        [d for d in os.listdir(base_dir) if d.isdigit() and len(d) == 8],
        reverse=True,
    )

    # 最新 N 日 + 1つ前のディレクトリ
    target_dates = sorted(all_dates[:n_days])
    if len(all_dates) > n_days:
        prev_of_first = sorted(all_dates[:n_days + 1])[0]
    else:
        prev_of_first = None

    metrics_list = []
    for i, date_str in enumerate(target_dates):
        curr_dir = str(base_dir / date_str)

        if i == 0 and prev_of_first:
            prev_dir = str(base_dir / prev_of_first)
        elif i > 0:
            prev_dir = str(base_dir / target_dates[i - 1])
        else:
            prev_dir = None

        m = compute_night_metrics(date_str, prev_dir, curr_dir)
        if m:
            metrics_list.append(m)

    if not metrics_list:
        print("データが見つかりませんでした。")
        sys.exit(1)

    print_weekly_report(metrics_list)
