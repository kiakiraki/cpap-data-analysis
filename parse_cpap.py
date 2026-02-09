"""
BMC/Hypnus CA820M CPAP データパーサー

独自 .edf フォーマット（標準 EDF ではない）を解析する。
ヘッダー: 512 バイト固定
データ: ファイルタイプごとに異なるフォーマット
"""

import struct
import os
from datetime import datetime, timedelta
from pathlib import Path


def parse_timestamp(data: bytes, offset: int) -> datetime:
    """8バイトのタイムスタンプをパースする"""
    year = struct.unpack_from("<H", data, offset)[0]
    month = data[offset + 2]
    day = data[offset + 3]
    hour = data[offset + 4]
    minute = data[offset + 5]
    second = data[offset + 6]
    subsecond = data[offset + 7]
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return datetime(1970, 1, 1)


def parse_header(data: bytes) -> dict:
    """512バイトのヘッダーをパースする"""
    version = data[0x00:0x08].decode("ascii", errors="replace").strip()
    device_id = data[0x08:0x20].decode("ascii", errors="replace").strip()
    patient_id = data[0x20:0x58].decode("ascii", errors="replace").strip()
    model_code = data[0x58:0xA8].decode("ascii", errors="replace").strip()

    start_time = parse_timestamp(data, 0xA8)
    end_time = parse_timestamp(data, 0xB0)

    header_size_str = data[0xB8:0xC0].decode("ascii", errors="replace").strip()
    firmware = data[0xC0:0xD8].decode("ascii", errors="replace").rstrip("\x00").strip()

    unit_and_params = data[0xD8:0x100].decode("ascii", errors="replace").strip()
    signal_name = data[0x100:0x160].decode("ascii", errors="replace").strip()

    # ヘッダーの ASCII 数値フィールド
    digital_min = data[0x160:0x170].decode("ascii", errors="replace").strip()
    digital_max = data[0x170:0x178].decode("ascii", errors="replace").strip()
    physical_min = data[0x178:0x180].decode("ascii", errors="replace").strip()
    physical_max = data[0x180:0x188].decode("ascii", errors="replace").strip()
    samples_per_record = data[0x1D0:0x1E0].decode("ascii", errors="replace").strip()

    return {
        "version": version,
        "device_id": device_id,
        "patient_id": patient_id,
        "model_code": model_code,
        "start_time": start_time,
        "end_time": end_time,
        "header_size": header_size_str,
        "firmware": firmware,
        "unit_and_params": unit_and_params,
        "signal_name": signal_name,
        "digital_min": digital_min,
        "digital_max": digital_max,
        "physical_min": physical_min,
        "physical_max": physical_max,
        "samples_per_record": samples_per_record,
    }


def parse_flow(filepath: str) -> dict:
    """flow.edf: 1バイト/サンプル、25Hz の呼吸フローデータ"""
    data = open(filepath, "rb").read()
    header = parse_header(data)
    samples = list(data[512:])
    duration_sec = len(samples) / 25.0
    return {
        "header": header,
        "sample_rate_hz": 25,
        "num_samples": len(samples),
        "duration_sec": duration_sec,
        "duration_min": duration_sec / 60,
        "min_value": min(samples) if samples else 0,
        "max_value": max(samples) if samples else 0,
        "mean_value": sum(samples) / len(samples) if samples else 0,
        "samples": samples,
    }


def parse_pressure(filepath: str) -> dict:
    """pressure.edf / realpresdata.edf / snoredata.edf: 2バイトLE/サンプル、25Hz"""
    data = open(filepath, "rb").read()
    header = parse_header(data)
    raw = data[512:]
    num_samples = len(raw) // 2
    samples = [struct.unpack_from("<H", raw, i * 2)[0] for i in range(num_samples)]
    duration_sec = num_samples / 25.0
    return {
        "header": header,
        "sample_rate_hz": 25,
        "num_samples": num_samples,
        "duration_sec": duration_sec,
        "duration_min": duration_sec / 60,
        "min_value": min(samples) if samples else 0,
        "max_value": max(samples) if samples else 0,
        "mean_value": sum(samples) / len(samples) if samples else 0,
        "samples": samples,
    }


def parse_event_file(filepath: str) -> dict:
    """ai/hi/snore/csa/leak: 16バイトイベントレコード"""
    data = open(filepath, "rb").read()
    header = parse_header(data)
    raw = data[512:]
    num_records = len(raw) // 16
    events = []
    for i in range(num_records):
        offset = i * 16
        event_type = struct.unpack_from("<I", raw, offset)[0]
        event_value = struct.unpack_from("<I", raw, offset + 4)[0]
        timestamp = parse_timestamp(raw, offset + 8)
        events.append(
            {"type": event_type, "value": event_value, "timestamp": timestamp}
        )
    return {"header": header, "num_events": num_records, "events": events}


def parse_apcp(filepath: str) -> dict:
    """apcp.edf: 16バイト/レコード、タイムスタンプ付き圧力変化記録"""
    data = open(filepath, "rb").read()
    header = parse_header(data)
    raw = data[512:]
    num_records = len(raw) // 16
    records = []
    for i in range(num_records):
        offset = i * 16
        pressure = struct.unpack_from("<I", raw, offset)[0]
        flags = struct.unpack_from("<I", raw, offset + 4)[0]
        timestamp = parse_timestamp(raw, offset + 8)
        records.append(
            {"pressure": pressure, "flags": flags, "timestamp": timestamp}
        )
    return {"header": header, "num_records": num_records, "records": records}


def parse_usetime(filepath: str) -> dict:
    """usetime.edf: 16バイト/レコード、使用時間記録"""
    data = open(filepath, "rb").read()
    header = parse_header(data)
    raw = data[512:]
    num_records = len(raw) // 16
    records = []
    for i in range(num_records):
        offset = i * 16
        value = struct.unpack_from("<I", raw, offset)[0]
        param = struct.unpack_from("<I", raw, offset + 4)[0]
        timestamp = parse_timestamp(raw, offset + 8)
        # param の下位バイトを分解（圧力設定値の可能性）
        param_bytes = struct.pack("<I", param)
        records.append(
            {
                "value": value,
                "param_raw": param,
                "param_bytes": list(param_bytes),
                "timestamp": timestamp,
            }
        )
    return {"header": header, "num_records": num_records, "records": records}


def parse_mvtvbr(filepath: str) -> dict:
    """mvtvbr.edf: 6バイト/レコード (3 x uint16_le) - MV/TV/BR データ"""
    data = open(filepath, "rb").read()
    header = parse_header(data)
    raw = data[512:]
    num_records = len(raw) // 6
    records = []
    for i in range(num_records):
        offset = i * 6
        v1 = struct.unpack_from("<H", raw, offset)[0]
        v2 = struct.unpack_from("<H", raw, offset + 2)[0]
        v3 = struct.unpack_from("<H", raw, offset + 4)[0]
        records.append({"field1": v1, "field2": v2, "field3": v3})
    return {"header": header, "num_records": num_records, "records": records}


def parse_config(filepath: str) -> dict:
    """config.edf: 200バイト/レコード の設定データ"""
    data = open(filepath, "rb").read()
    header = parse_header(data)
    raw = data[512:]
    record_size = 200
    num_records = len(raw) // record_size
    records = []
    for i in range(num_records):
        offset = i * record_size
        record_data = raw[offset : offset + record_size]
        # 末尾8バイトにタイムスタンプ
        timestamp = parse_timestamp(record_data, record_size - 8)
        # float32 値を抽出（設定圧力値の可能性）
        floats = []
        for j in range(0x70, 0xB0, 4):
            if j + 4 <= len(record_data):
                f = struct.unpack_from("<f", record_data, j)[0]
                floats.append(round(f, 2))
        records.append(
            {"timestamp": timestamp, "float_values": floats, "raw_hex": record_data.hex()}
        )
    return {"header": header, "num_records": num_records, "records": records}


def parse_spo2bpm(filepath: str) -> dict:
    """spo2bpm.edf: SpO2/心拍数データ"""
    data = open(filepath, "rb").read()
    header = parse_header(data)
    raw = data[512:]
    # 2バイト/サンプルとして解析
    num_samples = len(raw) // 2
    samples = [struct.unpack_from("<H", raw, i * 2)[0] for i in range(num_samples)]
    non_zero = [s for s in samples if s > 0]
    return {
        "header": header,
        "num_samples": num_samples,
        "non_zero_count": len(non_zero),
        "data_bytes": len(raw),
        "min_nonzero": min(non_zero) if non_zero else 0,
        "max_nonzero": max(non_zero) if non_zero else 0,
    }


def parse_day(day_dir: str) -> dict:
    """1日分の全ファイルをパースする"""
    day_name = os.path.basename(day_dir)
    result = {"date": day_name, "files": {}}

    parsers = {
        "flow": parse_flow,
        "pressure": parse_pressure,
        "realpresdata": parse_pressure,
        "snoredata": parse_pressure,
        "ai": parse_event_file,
        "hi": parse_event_file,
        "snore": parse_event_file,
        "csa": parse_event_file,
        "leak": parse_event_file,
        "apcp": parse_apcp,
        "usetime": parse_usetime,
        "mvtvbr": parse_mvtvbr,
        "config": parse_config,
        "spo2bpm": parse_spo2bpm,
        "difleak": parse_spo2bpm,
    }

    for ftype, parser in parsers.items():
        filepath = os.path.join(day_dir, f"{day_name}_{ftype}.edf")
        if os.path.exists(filepath):
            try:
                result["files"][ftype] = parser(filepath)
            except Exception as e:
                result["files"][ftype] = {"error": str(e)}

    return result


def print_day_summary(day_data: dict):
    """1日分のデータのサマリーを表示"""
    print(f"\n{'='*70}")
    print(f" CPAP データサマリー: {day_data['date']}")
    print(f"{'='*70}")

    # ヘッダー情報（最初に見つかったファイルから）
    for ftype, fdata in day_data["files"].items():
        if "header" in fdata:
            h = fdata["header"]
            print(f"\n[装置情報]")
            print(f"  バージョン: {h['version']}")
            print(f"  ファームウェア: {h['firmware']}")
            print(f"  デバイスID: {h['patient_id']}")
            print(f"  時間範囲: {h['start_time']} → {h['end_time']}")
            break

    # Flow データ
    if "flow" in day_data["files"]:
        f = day_data["files"]["flow"]
        if "error" not in f:
            print(f"\n[呼吸フロー (flow)]")
            print(f"  サンプル数: {f['num_samples']:,}")
            print(f"  推定サンプルレート: {f['sample_rate_hz']} Hz")
            print(f"  記録時間: {f['duration_min']:.1f} 分 ({f['duration_sec']/3600:.2f} 時間)")
            print(f"  値の範囲: {f['min_value']} - {f['max_value']} (平均: {f['mean_value']:.1f})")

    # Pressure データ
    if "pressure" in day_data["files"]:
        p = day_data["files"]["pressure"]
        if "error" not in p:
            print(f"\n[圧力データ (pressure)]")
            print(f"  サンプル数: {p['num_samples']:,}")
            print(f"  値の範囲: {p['min_value']} - {p['max_value']} (平均: {p['mean_value']:.1f})")

    # APCP データ
    if "apcp" in day_data["files"]:
        a = day_data["files"]["apcp"]
        if "error" not in a and a["num_records"] > 0:
            print(f"\n[APAP 圧力変化 (apcp)]")
            print(f"  レコード数: {a['num_records']}")
            first = a["records"][0]
            last = a["records"][-1]
            print(f"  時間範囲: {first['timestamp']} → {last['timestamp']}")
            pressures = [r["pressure"] for r in a["records"]]
            print(f"  圧力値範囲: {min(pressures)} - {max(pressures)} (平均: {sum(pressures)/len(pressures):.1f})")
            print(f"  圧力値（cmH2O推定）: {min(pressures)/10:.1f} - {max(pressures)/10:.1f}")

    # AI（無呼吸）イベント
    if "ai" in day_data["files"]:
        ai = day_data["files"]["ai"]
        if "error" not in ai:
            print(f"\n[無呼吸イベント (AI)]")
            print(f"  イベント数: {ai['num_events']}")
            for i, e in enumerate(ai["events"][:5]):
                print(f"  #{i+1}: type={e['type']}, duration={e['value']}s, time={e['timestamp']}")
            if ai["num_events"] > 5:
                print(f"  ... (残り {ai['num_events']-5} イベント)")

    # HI（低呼吸）イベント
    if "hi" in day_data["files"]:
        hi = day_data["files"]["hi"]
        if "error" not in hi:
            print(f"\n[低呼吸イベント (HI)]")
            print(f"  イベント数: {hi['num_events']}")
            for i, e in enumerate(hi["events"][:5]):
                print(f"  #{i+1}: type={e['type']}, duration={e['value']}s, time={e['timestamp']}")

    # CSA（中枢性無呼吸）イベント
    if "csa" in day_data["files"]:
        csa = day_data["files"]["csa"]
        if "error" not in csa:
            print(f"\n[中枢性無呼吸イベント (CSA)]")
            print(f"  イベント数: {csa['num_events']}")

    # Snore イベント
    if "snore" in day_data["files"]:
        snore = day_data["files"]["snore"]
        if "error" not in snore:
            print(f"\n[いびきイベント (snore)]")
            print(f"  イベント数: {snore['num_events']}")

    # mvtvbr データ
    if "mvtvbr" in day_data["files"]:
        m = day_data["files"]["mvtvbr"]
        if "error" not in m and m["num_records"] > 0:
            print(f"\n[換気量/呼吸回数 (mvtvbr)]")
            print(f"  レコード数: {m['num_records']}")
            f1 = [r["field1"] for r in m["records"]]
            f2 = [r["field2"] for r in m["records"]]
            f3 = [r["field3"] for r in m["records"]]
            print(f"  Field1 範囲: {min(f1)} - {max(f1)} (平均: {sum(f1)/len(f1):.1f})")
            print(f"  Field2 範囲: {min(f2)} - {max(f2)} (平均: {sum(f2)/len(f2):.1f})")
            print(f"  Field3 範囲: {min(f3)} - {max(f3)} (平均: {sum(f3)/len(f3):.1f})")
            print(f"  最初の5レコード:")
            for i, r in enumerate(m["records"][:5]):
                print(f"    #{i+1}: {r['field1']}, {r['field2']}, {r['field3']}")

    # Usetime データ
    if "usetime" in day_data["files"]:
        u = day_data["files"]["usetime"]
        if "error" not in u:
            print(f"\n[使用時間 (usetime)]")
            print(f"  レコード数: {u['num_records']}")
            for i, r in enumerate(u["records"]):
                print(f"  #{i+1}: value={r['value']}, param=0x{r['param_raw']:08x}, time={r['timestamp']}")

    # SpO2/BPM
    if "spo2bpm" in day_data["files"]:
        s = day_data["files"]["spo2bpm"]
        if "error" not in s:
            print(f"\n[SpO2/心拍数 (spo2bpm)]")
            print(f"  サンプル数: {s['num_samples']}")
            print(f"  非ゼロサンプル: {s['non_zero_count']}")
            if s["non_zero_count"] > 0:
                print(f"  非ゼロ値範囲: {s['min_nonzero']} - {s['max_nonzero']}")

    # Config
    if "config" in day_data["files"]:
        c = day_data["files"]["config"]
        if "error" not in c:
            print(f"\n[装置設定 (config)]")
            print(f"  レコード数: {c['num_records']}")
            for i, r in enumerate(c["records"][:3]):
                print(f"  #{i+1}: time={r['timestamp']}")
                if r["float_values"]:
                    print(f"        float値: {r['float_values']}")


if __name__ == "__main__":
    import sys

    base_dir = Path(__file__).parent / "DATAFILE"

    if len(sys.argv) > 1:
        target_date = sys.argv[1]
    else:
        # デフォルト: 20220528（データが充実している日）
        target_date = "20220528"

    day_dir = base_dir / target_date
    if not day_dir.exists():
        print(f"ディレクトリが見つかりません: {day_dir}")
        sys.exit(1)

    print(f"パース中: {day_dir}")
    day_data = parse_day(str(day_dir))
    print_day_summary(day_data)
