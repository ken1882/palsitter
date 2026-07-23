**| [English](README.md) | [繁體中文](README_tw.md) | 日本語 |**

# Palsitter

#### [![GitHub release](https://img.shields.io/github/v/release/ken1882/palsitter?color=4e4c97)](https://github.com/ken1882/palsitter/releases) [![GitHub commit activity](https://img.shields.io/github/commit-activity/m/ken1882/palsitter?color=4e4c97)](https://github.com/ken1882/palsitter/commits) [![GitHub issues](https://img.shields.io/github/issues/ken1882/palsitter?color=4e4c97)](https://github.com/ken1882/palsitter/issues)

Palsitter は Web GUI を備えたクロスプラットフォームのゲームサーバー管理ツールです。
専用サーバーを継続的に運用しながら、インストール、更新、ライフサイクル操作、バックアップ、
プレイヤー、設定、ログを一つの画面で管理できます。

Palsitter は現在、Palworld に完全対応しています。Satisfactory は機能のないプレースホルダー
にすぎないため、使用しないでください。

## 機能

- **複数サーバー管理**：一つのインターフェースから、ゲームサーバーのプロファイルを作成、
  複製、名前変更、削除、管理できます。
- **起動後はおまかせ**：プロファイルの設定に従って SteamCMD でサーバーをインストール・
  ダウンロードして起動し、クラッシュ後の自動再起動、プレイヤーが接続していないときの
  更新に伴う自動再起動を行います。手動での再起動や更新は不要です。
- **サーバーとワールド設定**：インターフェースからサーバーとゲームの設定を直接編集でき、
  各設定の効果を説明する項目も表示します。
- **セーブデータとバックアップ**：バックアップの作成・復元、定期バックアップのスケジュール、
  移行や復旧に必要なセーブデータの保持を行います。
- **ツールと監査**：サーバー出力の確認、対応操作の実行、監査履歴の確認、ゲーム固有の
  ユーティリティを一つのインターフェースから利用できます。
- **マルチプラットフォーム対応**：Windows ポータブル版、ネイティブ Linux、Docker Compose、
  systemd を利用できます。

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

認証付きリバースプロキシと適切なファイアウォールルールを用意せずに、Web UI を直接
インターネットへ公開しないでください。

### Docker

Linux イメージと Compose 設定が含まれています。ビルドして起動するには：

```bash
mkdir -p docker-volumns/config docker-volumns/profile docker-volumns/logs
docker compose up -d --build
```

Compose はホストネットワークを使用します。これにより、管理対象の各 Palworld インスタンスが
割り当てられたゲーム、クエリ、REST ポートを受け取れます。実行時データはイメージの外部に
保存されます：

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

Docker ホストで [http://127.0.0.1:22368/](http://127.0.0.1:22368/) を開きます。バインド
アドレスやポートを変更するには、Compose 環境で `PALSITTER_HOST` または `PALSITTER_PORT`
を設定してください。

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

Linux シェル版はデフォルトで実行時データを `data/` に保存します：

```text
data/config/    Palsitter の設定
data/profile/   インスタンス、Palworld のインストール、セーブ、バックアップ
data/logs/      アプリケーションログ
```

更新または移行の前に `data/config` と `data/profile` をバックアップしてください。別の場所を
使用する場合は、インストール時と実行時で `PALSITTER_DATA_DIR` に同じ値を設定します：

```bash
export PALSITTER_DATA_DIR=/srv/palsitter-data
./script/linux/palsitter.sh install
./script/linux/palsitter.sh run
```

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
python test.py
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
