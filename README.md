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

### ヘッダー構造 (512バイト固定)

全 `.edf` ファイル共通で **512バイト (0x200)** のヘッダーを持つ。

| オフセット | サイズ | フィールド |
|-----------|--------|-----------|
| `0x000` | 8 | フォーマットバージョン (ASCII) |
| `0x008` | 24 | デバイスシリアル番号 (ASCII) |
| `0x020` | 56 | 患者ID / デバイスID (ASCII) |
| `0x058` | 80 | モデルコード + 予約領域 |
| `0x0A8` | 8 | **開始タイムスタンプ** (バイナリ) |
| `0x0B0` | 8 | **終了タイムスタンプ** (バイナリ) |
| `0x0B8` | 8 | ヘッダーサイズ (ASCII) |
| `0x0C0` | 24 | ファームウェア/デバイス名 (ASCII) |
| `0x0D8` | 40 | 単位 + パラメータ (ASCII) |
| `0x100` | 96 | データタイプ名 (ASCII) |
| `0x160` | 112 | Digital/Physical min/max (ASCII) |
| `0x1D0` | 48 | サンプル数/予約 (ASCII) |

#### タイムスタンプ形式 (8バイト)

```
Offset  Size  Type        Description
0       2     uint16_le   Year
2       1     uint8       Month  (1-12)
3       1     uint8       Day    (1-31)
4       1     uint8       Hour   (0-23)
5       1     uint8       Minute (0-59)
6       1     uint8       Second (0-59)
7       1     uint8       Sub-second
```

> ヘッダーの開始/終了タイムスタンプは「カレンダー上の時間範囲」を示し、
> データの実際の記録時間とは一致しない。実際の記録時間はデータサイズとサンプルレートから算出する。

### データフォーマット (ファイルタイプ別)

#### 連続波形データ (タイムスタンプなし)

ヘッダー直後から連続サンプルが格納される。

| ファイル | サンプルサイズ | サンプルレート | データ型 | 内容 |
|---------|-------------|-------------|---------|------|
| flow | 1 byte | 25 Hz | uint8 | 呼吸フロー波形 (L/min) |
| pressure | 2 bytes | 25 Hz | uint16_le | 圧力波形 (cmH2O) |
| realpresdata | 2 bytes | 25 Hz | uint16_le | 実測圧力波形 |
| snoredata | 2 bytes | 25 Hz | uint16_le | いびき波形 |

- flow と pressure のサンプル数は常に完全一致
- サンプルレート 25 Hz は最短セッションのサンプル数とタイムスタンプ差から算出

#### 換気量・呼吸回数データ (mvtvbr)

**レコードサイズ: 6バイト** (3 x uint16_le)、**サンプリング間隔: 5秒**

```
Offset  Size  Type        Field
0       2     uint16_le   Field1 (推定: Tidal Volume, 一回換気量 mL)
2       2     uint16_le   Field2 (推定: Breathing Rate, 呼吸回数/分)
4       2     uint16_le   Field3 (推定: Minute Ventilation 関連)
```

#### 差分リークデータ (difleak)

**サンプルサイズ: 1バイト**、**サンプリング間隔: 2.5秒**、単位: **L/min**

- 各バイトはその時点のリーク量 (L/min) を表す
- difleak のサンプル数は mvtvbr レコード数の正確に **2倍** (5秒÷2=2.5秒)
- 機械が表示する「リーク」は、1晩分の difleak 全サンプルの算術平均に一致

#### イベントデータ (ai / hi / snore / csa / leak)

**レコードサイズ: 16バイト**、イベント発生時のみ記録。

```
Offset  Size  Type        Description
0       4     uint32_le   イベントタイプ (通常 1)
4       4     uint32_le   イベント持続時間 (秒)
8       8     timestamp   発生時刻
```

#### APAP圧力変化記録 (apcp)

**レコードサイズ: 16バイト**、約1秒間隔で圧力値を記録。

```
Offset  Size  Type        Description
0       4     uint32_le   圧力値 (÷10 で cmH2O)
4       4     uint32_le   フラグ (通常 0)
8       8     timestamp   記録時刻
```

#### 使用時間記録 (usetime)

**レコードサイズ: 16バイト**

```
Offset  Size  Type        Description
0       4     uint32_le   セッション持続時間 (秒)
4       4     uint32_le   パラメータ (圧力設定の可能性あり)
8       8     timestamp   記録時刻
```

usetime レコードには2種類が存在する:

| 種別 | タイムスタンプの特徴 | 意味 |
|------|---------------------|------|
| **バウンダリ** | 分・秒がともに `00` | ディレクトリ境界で切断された夕方セッションの持続時間 |
| **非バウンダリ** | 分・秒が非ゼロ | 当ディレクトリの朝セッションの持続時間 |

#### 装置設定 (config)

**レコードサイズ: 200バイト** (192バイトのデータ + 8バイトのタイムスタンプ)。
内部に **float32 (IEEE 754 LE)** で圧力設定値が含まれる。

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

- ディレクトリ境界は **不定の整時** (01:00, 02:00, ..., 07:00 など、日によって異なる)
- 1晩の使用時間 = `dir(X-1)` のバウンダリ usetime + `dir(X)` の非バウンダリ usetime
- この方式で計算した使用時間は、機械ディスプレイの表示値と **±0.1h 以内** で一致することを確認済み

#### イベント・リーク・呼吸データの結合

| データ種別 | 結合方法 |
|-----------|---------|
| イベント (ai/hi/csa/snore) | dir(X) のイベントのみ使用 (※) |
| difleak | dir(X-1) の末尾 (夕方按分) + dir(X) の先頭 (朝按分) を結合し平均 |
| mvtvbr (呼吸指標) | 同上 (usetime 比率で按分) |
| apcp (圧力) | 両ディレクトリの値を結合 |

> ※ SD カードのイベントデータは各ディレクトリの最初の約24分間にのみ記録される。
> 夕方セッション部分にはイベントが記録されないため、dir(X) のイベントのみを使用する。

#### 按分の計算方法

```
夕方比率 = バウンダリ usetime / usetime 合計
朝比率   = 非バウンダリ usetime 合計 / usetime 合計

夕方 difleak = ディレクトリの difleak データの末尾 (夕方比率 × 全サンプル数) 個
朝 difleak   = ディレクトリの difleak データの先頭 (朝比率 × 全サンプル数) 個
```

### データ整合性の検証

複数日分のデータでクロスバリデーションを実施し、フォーマット解釈の正確性を確認した。

- **flow と pressure**: サンプル数が全日完全一致
- **flow (25Hz) と mvtvbr (5秒間隔)**: 所要時間の差は最大 7 秒以内
- **APCP 圧力値 ÷ 10**: config の float32 設定値と一致
- **使用時間**: 機械ディスプレイの値と全日 ±0.1h 以内で一致
- **リーク**: 機械ディスプレイの値と概ね一致 (±0.5 L/min)

### 既知の制約・未解決事項

- **AHI の精度**: SD カードにはディレクトリ開始後の約24分間のイベントのみ記録されるため、機械の値と構造的な差が生じる
- **usetime と flow の 2倍関係**: usetime レコード合計値が flow データ時間のちょうど2倍になる。原因は未特定
- **mvtvbr の Field3**: 分時換気量の可能性があるが、単位やスケーリングが未特定
- **spo2bpm**: パルスオキシメーターセンサー未接続のため未検証

## 関連プロジェクト

- [OSCAR](https://www.sleepfiles.com/OSCAR/) — オープンソースの CPAP データビューア。
  BMC ローダー (`bmc_loader.cpp`) に同シリーズの解析実装がある。
