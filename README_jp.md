**| [English](README.md) | [繁體中文](README_tw.md) | 日本語 |**

# Palsitter

#### [![GitHub release](https://img.shields.io/github/v/release/ken1882/palsitter?color=4e4c97)](https://github.com/ken1882/palsitter/releases) [![GitHub commit activity](https://img.shields.io/github/commit-activity/m/ken1882/palsitter?color=4e4c97)](https://github.com/ken1882/palsitter/commits) [![GitHub issues](https://img.shields.io/github/issues/ken1882/palsitter?color=4e4c97)](https://github.com/ken1882/palsitter/issues)

<p align="center"><img src="assets/gui/brand/palsitter.png" alt="Palsitter logo" width="256"></p>

**Palworld Server Babysitter** · [GitHub](https://github.com/ken1882/palsitter) · [Windows x64 ポータブル版](https://github.com/ken1882/palsitter/releases)

Palsitter は Web GUI を備えたクロスプラットフォームのゲームサーバー管理ツールです。
専用サーバーを継続的に運用しながら、インストール、更新、ライフサイクル操作、バックアップ、
プレイヤー、設定、ログを一つの画面で管理できます。

Palsitter は現在、Palworld に完全対応しています。Satisfactory は機能のないプレースホルダー
にすぎないため、使用しないでください。

サーバーを作成して起動した後は、日常的なインストール、更新、復旧、バックアップを Web GUI
から任せることができ、別の黒いコンソールウィンドウを開く必要はありません。Palsitter は
小規模なサーバーを長時間運用しながら、状態と出力を一つの画面にまとめるためのツールです。

## 機能

- **複数サーバー管理**：一つのインターフェースから、ゲームサーバーのプロファイルを作成、
  複製、名前変更、削除、管理できます。
- **起動後はおまかせ**：プロファイルの設定に従って SteamCMD でサーバーをインストール・
  ダウンロードして起動し、クラッシュ後の自動再起動、プレイヤーが接続していないときの
  更新に伴う自動再起動を行います。スケジュール再起動、メモリ使用量による再起動、再起動履歴、
  短時間の連続クラッシュに対する自己修復にも対応し、ロールバック前には安全バックアップを作成します。
- **サーバーとワールド設定**：インターフェースからサーバーとゲームの設定を直接編集でき、
  各設定の効果を説明する項目も表示します。
- **セーブデータとバックアップ**：バックアップの作成・復元、定期バックアップ、ワールド切り替え、
  シングルプレイまたは協力プレイのセーブデータから専用サーバーへのプレイヤーデータ移行に対応します。
  セーブデータを上書きする可能性がある操作の前には安全バックアップを作成します。
- **プレイヤーとマップ**：オンライン、オフライン、BAN 済みプレイヤーの確認、キック・BAN を行えます。
  内蔵マップにはファストトラベル地点、プレイヤー、拠点を表示できます。
- **MOD とツール**：インストール済み Pak MOD を管理できます。Windows では UE4SS と Lua MOD の場所も
  扱えますが、MOD 自体のダウンロードは行いません。ファイアウォールの確認・修復ツールでサーバー実行ファイルと
  UDP ポートを確認できますが、ルーターのポート転送は自分で設定してください。
- **ログと監査**：Web GUI からリアルタイムのサーバー出力、状態、メトリクス、対応操作、操作履歴を確認できます。
- **マルチプラットフォーム対応**：Windows ポータブル版、ネイティブ Linux、Docker Compose、
  systemd を利用できます。

## クイックスタート（Palworld）

### Windows ポータブル版

1. [Releases](https://github.com/ken1882/palsitter/releases) から `Palsitter-win-x64.7z` をダウンロードし、書き込み可能なディレクトリに展開して `Palsitter.exe` を起動します。
2. 左上の **インスタンスを追加** を選択します。既存のワールドをインポートする場合は **参照** を選び、対応する `Level.sav` を指定します。セーブデータがない場合はそのまま確定します。
3. インスタンスを起動し、SteamCMD と Palworld 専用サーバーのインストール・起動が完了するまで待ちます。新しいサーバーでは必要に応じて admin password が自動生成され、GUI が使用する REST API も自動的に有効になります。
4. 状態が起動中になり、Overview パネルにメトリクスが表示されたら準備完了です。サーバー出力と Palsitter の操作履歴は Web GUI に表示されます。

### ソースから実行

リポジトリを clone して `requirements.txt` のパッケージをインストールした後、次を実行します：

```bash
git clone https://github.com/ken1882/palsitter.git
cd palsitter
python -m pip install -r requirements.txt
python gui.py
```

[http://127.0.0.1:22368/](http://127.0.0.1:22368/) を開き、同じインスタンス追加手順に進みます。Linux 環境では下記のインストーラーも使用できます。

### シングルプレイまたは協力プレイのセーブデータを移行する場合

セーブデータをインポートした後、まずプレイヤーが専用サーバーでキャラクターを作成します。その後サーバーを停止し、**Home → Utils → Player ID migration** を実行してください。インポートしたセーブデータに利用できる名前がない場合は、先にプレイヤー名キャッシュを作成して、移行元と移行先を間違えないようにします。移行ツールは安全バックアップを先に作成します。

Windows 版で右上の **X** を押すと Palsitter はシステムトレイに最小化されます。完全に終了するには、トレイアイコンの **Exit**、または **Home → Utils → Shut down Palsitter** を使用してください。

## インストール

### Windows

[Releases](https://github.com/ken1882/palsitter/releases) から最新のポータブルアーカイブを
ダウンロードし、書き込み可能なディレクトリに展開して `Palsitter.exe` を起動してください。
ポータブル版は設定、プロファイル、ログをローカルの `data/` ディレクトリに保存します。

### ネイティブ Linux

マシン上で直接サーバーを動かす場合は、先に必要な Python 環境を用意して、このリポジトリを
clone してください。

プロジェクトのルートディレクトリで実行してください：

```bash
chmod +x script/linux/palsitter.sh
./script/linux/palsitter.sh install
./script/linux/palsitter.sh run
```

GUI の起動後、[http://127.0.0.1:22368/](http://127.0.0.1:22368/) を開きます。デフォルトでは
UI は localhost のみで待ち受けます。リモート管理には SSH トンネルを使用してください：

```bash
ssh -L 22368:127.0.0.1:22368 user@server
```

インストーラーはデフォルトで `venv` を使用し、`asdf`、`pipenv`、`uv` にも対応しています：

```bash
PALSITTER_PYTHON_MANAGER=uv ./script/linux/palsitter.sh install
PALSITTER_PYTHON_MANAGER=uv ./script/linux/palsitter.sh run
```

必要に応じて、`run` の後ろに `gui.py` の追加引数を渡せます：

```bash
./script/linux/palsitter.sh run --host 0.0.0.0 --port 22368
```

Home → 設定で Web UI を選択したネットワークインターフェースにバインドできます。
パネルをリモートから利用する場合は、マシンを信頼できるイントラネットまたは VPN の内側に
置き、Basic Auth と適切なファイアウォールルールを有効にしてください。CLI と環境変数の
ホスト設定は保存済み設定より優先されます。

### Docker

Linux イメージと Compose 設定が含まれています。ビルドして起動するには：

```bash
./script/linux/start-docker.sh
```

Compose は Palsitter の Web UI を Docker ホストの 22368 ポートに公開します。実行時データは
イメージの外部に保存されます：

| ホストパス | 内容 |
| --- | --- |
| `./docker-volumns/config` | Palsitter の設定 |
| `./docker-volumns/profile` | Palworld のインストール、セーブ、バックアップ、インスタンスデータ |
| `./docker-volumns/logs` | アプリケーションログ |

コンテナは UID `1000` で実行されます。必要に応じて、起動前に volume ディレクトリを
そのユーザーが書き込めるようにしてください：

```bash
sudo chown -R 1000:1000 docker-volumns
```

Docker ホストで [http://127.0.0.1:22368/](http://127.0.0.1:22368/) を開きます。コンテナのバインド
アドレスやポートを変更するには、Compose 環境で `PALSITTER_HOST` または `PALSITTER_PORT`
を設定してください。ホスト側ポートはデフォルトで localhost のみにバインドされます。他の
マシンから接続する場合は `compose.yaml` のホスト側マッピングを変更してください。

### systemd

先に Python 環境をインストールし、現在の checkout 用サービスをインストールして起動します：

```bash
./script/linux/palsitter.sh install
sudo ./script/linux/systemd-install.sh
```

サービスの状態とログを確認します：

```bash
systemctl status palsitter
journalctl -u palsitter -f
```

## データと更新

ソースからの直接実行と Linux シェル版は、同じプロジェクトルートのディレクトリを使用します：

```text
config/    Palsitter の設定
profile/   インスタンス、Palworld のインストール、セーブ、バックアップ
logs/      アプリケーションログ
```

更新または移行の前に `config/` と `profile/` をバックアップしてください。

ソース checkout の更新：

```bash
git pull
./script/linux/palsitter.sh install
./script/linux/palsitter.sh run
```

Docker 版はイメージを再ビルドして更新します：

```bash
docker compose build --pull
docker compose up -d
```

## Windows Electron リリースのビルド

ローカルビルドには Windows PowerShell、Node.js 24、`pip` を含む Python 3.12、
Git for Windows、7-Zip が必要です。リポジトリのルートディレクトリで実行してください：

```powershell
.\build.bat
```

このバッチファイルは `python` が Python 3.12 を参照していない場合、ステージング前に停止します。
各スクリプトにはプロセス単位の PowerShell 実行ポリシーのバイパスを適用し、ビルドまたは
パッケージ済みランタイムの検証に失敗した時点で直ちに終了します。

展開済みアプリケーションは `desktop/dist/win-unpacked/` に出力されます。ポータブル版の
アーカイブと SHA-256 チェックサムは `desktop/dist/` に出力されます。パッケージの詳細と
トラブルシューティングについては
[Windows Electron Release](docs/shared/features/windows-electron-release.md#building-locally)
を参照してください。

## ドキュメント

- [共通ドキュメント](docs/shared/README.md) — アプリケーションシェル、ストレージ、
  ローカライズ、ファイルブラウザー、共通 UI の動作。
- [Palworld ドキュメント](docs/games/palworld/README.md) — 概要、設定、マップ、プレイヤー、
  MOD、セーブ、バックアップ、ポート、インストール、ライフサイクル。
- [Satisfactory ドキュメント](docs/games/satisfactory/README.md) — プレースホルダーの仕様と
  明示された制限。
- [ドキュメント全体の索引](docs/README.md)

## 開発

`requirements.txt` から開発用依存関係をインストールして、テストを実行します：

```bash
python -m pytest -q
```

プロジェクトのテストワークフロー：

```bash
python run_tests.py
```

変更を提出する前に `python -m compileall -q .` も実行してください。GUI を変更する場合は、
対応する Playwright テストも更新してください。

## 貢献とサポート

バグ報告や機能提案は [GitHub Issues](https://github.com/ken1882/palsitter/issues) から
受け付けています。Palsitter のバージョン、OS、選択したゲーム、再現手順、関連ログを記載
してください。動作を変更する Pull Request には対象を絞ったテストを追加し、ユーザー向けの
仕様が変わる場合はドキュメントも更新してください。

現在の貢献方法については [Contributing](https://github.com/ken1882/palsitter/contribute)
を参照してください。
