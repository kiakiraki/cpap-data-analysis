# BMC/Hypnus CA820M CPAP Data Analysis

BMC (Beijing Mechanical & Electrical) 製 CPAP 装置 **Hypnus CA820M** の SD カードデータをパース・集計・可視化するツール群。

ファイル拡張子は `.edf` だが、標準 EDF (European Data Format) とは異なる **独自バイナリフォーマット** である。

## 主な機能

| スクリプト | 用途 |
|-----------|------|
| `parse_cpap.py` | 1日分の全ファイルタイプをパースしてダンプ |
| `weekly_report.py` | 直近 N 日間の診断レポートをターミナル出力 |
| `visualize_history.py` | 全期間の使用時間・AHI・AI・リークをグラフ化 |
| `export_csv.py` | 全期間データを CSV にエクスポート |

## 必要環境

- Python 3.10+
- NumPy
- Matplotlib

```bash
pip install numpy matplotlib
```

## 使い方

SD カードの `DATAFILE/` ディレクトリと `CONFIG/` ディレクトリをプロジェクトルートに配置する。

```
project-root/
├── CONFIG/
│   └── config.bin
├── DATAFILE/
│   └── YYYYMMDD/
│       ├── YYYYMMDD_flow.edf
│       ├── YYYYMMDD_pressure.edf
│       ├── ...
```

### 1日分のデータをパース

```bash
python3 parse_cpap.py 20220528
```

### 週間レポート

```bash
# 直近 7 日間 (デフォルト)
python3 weekly_report.py

# 直近 14 日間
python3 weekly_report.py 14
```

出力例:
```
══════════════════════════════════════════════════════════════════════════════
  CPAP 週間診断レポート
  装置: Hypnus CA820M (APAP)
  期間: XXXX-XX-XX ～ XXXX-XX-XX
══════════════════════════════════════════════════════════════════════════════
        日付   使用時間    AHI   AI   HI  CSA  いびき  リーク  圧力90%
              (時間)  (/h)  (件)  (件)  (件)  (件)  (L/min)  (cmH2O)
──────────────────────────────────────────────────────────────────────────────
  XXXX-XX-XX      6.7    1.5   10    0    0     4     1.0      6.7
  ...
```

### 全期間トレンド可視化

```bash
python3 visualize_history.py                # → cpap_history.png
python3 visualize_history.py output.png     # 出力先を指定
```

4パネル構成 (散布図 + 30日移動平均):

1. **使用時間** — コンプライアンス基準 (4h) ライン付き
2. **AHI** — 重症度バンド (Normal / Mild / Moderate / Severe)
3. **無呼吸イベント数 (AI)**
4. **リーク (L/min)**

### CSV エクスポート

```bash
python3 export_csv.py                # → cpap_all_data.csv
python3 export_csv.py output.csv     # 出力先を指定
```

20カラム: `date`, `usage_hours`, `ahi`, `ai_count`, `hi_count`, `csa_count`, `snore_count`,
`ai_total_duration_sec`, `hi_total_duration_sec`, `leak_avg_lpm`,
`pressure_min/max/mean/median/p90/p95`, `br_mean/median`, `tv_mean/median`

## 解析で判明した仕様

詳細は [ANALYSIS.md](ANALYSIS.md) を参照。

### ファイルフォーマット概要

全 `.edf` ファイルは **512 バイトの固定長ヘッダー** + データ本体で構成される。

| ファイル | レコードサイズ | サンプルレート | 内容 |
|---------|-------------|-------------|------|
| flow | 1 byte | 25 Hz | 呼吸フロー波形 (L/min) |
| pressure | 2 bytes | 25 Hz | 圧力波形 (cmH2O) |
| realpresdata | 2 bytes | 25 Hz | 実測圧力波形 |
| snoredata | 2 bytes | 25 Hz | いびき波形 |
| mvtvbr | 6 bytes | 0.2 Hz (5秒) | 換気量・呼吸回数 |
| difleak | 1 byte | 0.4 Hz (2.5秒) | マスクリーク (L/min) |
| ai / hi / csa / snore / leak | 16 bytes | イベント発生時 | 呼吸イベント |
| apcp | 16 bytes | ~1 Hz | APAP 圧力変化 |
| usetime | 16 bytes | セッション終了時 | 使用時間記録 |
| config | 200 bytes | 設定変更時 | 装置設定スナップショット |

### セッションモデル (1晩 = 2ディレクトリ)

この装置は、1晩のCPAPセッションを **2つのディレクトリ** にまたがって記録する:

```
         ディレクトリ dir(X-1)              ディレクトリ dir(X)
    ┌──────────────────────────────┐ ┌──────────────────────────────┐
    │  朝セッション  │  夕方セッション │ │  朝セッション  │  夕方セッション │
    │  (前の夜の続き)│  (今夜の始まり) │ │  (今夜の続き)  │  (次の夜の始まり)│
    └──────────────────────────────┘ └──────────────────────────────┘
                                   ↑
                             ディレクトリ境界
                           (XX:00:00 の整時)
```

1晩の使用時間 = `dir(X-1)` のバウンダリ usetime + `dir(X)` の非バウンダリ usetime

この方式で計算した使用時間は、機械ディスプレイの表示値と **±0.1h 以内** で一致することを確認済み。

> **初期データ (2022年前半)**: 1ディレクトリ = 1晩のフォーマットで、バウンダリレコードが存在しない。
> 非バウンダリレコードの最大値を使用することで、新旧どちらの形式にも対応している。

### 既知の制約

- **AHI の精度**: SD カードにはディレクトリ開始後の約24分間のイベントのみ記録される。
  機械は全セッションのイベントを内部保持しているため、AHI に構造的な差が生じる。
- **usetime と flow の 2倍関係**: usetime レコード合計値が flow データ時間のちょうど2倍になる。原因は未特定。

## 関連プロジェクト

- [OSCAR](https://www.sleepfiles.com/OSCAR/) — オープンソースの CPAP データビューア。
  BMC ローダー (`bmc_loader.cpp`) に同シリーズの解析実装がある。
