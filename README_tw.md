**| [English](README.md) | 繁體中文 | [日本語](README_jp.md) |**

# Palsitter

#### [![GitHub release](https://img.shields.io/github/v/release/ken1882/palsitter?color=4e4c97)](https://github.com/ken1882/palsitter/releases) [![GitHub commit activity](https://img.shields.io/github/commit-activity/m/ken1882/palsitter?color=4e4c97)](https://github.com/ken1882/palsitter/commits) [![GitHub issues](https://img.shields.io/github/issues/ken1882/palsitter?color=4e4c97)](https://github.com/ken1882/palsitter/issues)

Palsitter 是一套具備網頁 GUI 的跨平台遊戲伺服器管理工具，適合長時間持續執行
專用伺服器，並將安裝、更新、生命週期操作、備份、玩家、設定與記錄集中於同一個介面。

目前完整支援幻獸帕魯。滿意工廠目前只是沒有功能的花瓶所以不要用他。

## 功能

- **多伺服器管理**：從單一介面建立、複製、重新命名、刪除及管理不同的遊戲伺服器設定檔。
- **啟動後解放雙手**：啟動後立即透過 steamcmd 根據設定檔安裝並下載啟動伺服器，崩潰後自動重啟，偵測到遊戲有更新後若無玩家連線將會自動重啟更新。不需要再手動重啟與更新。
- **伺服器與世界設定**：在介面上直接編輯伺服器與遊戲選項，並提供說明項目了解更改影響。
- **存檔與備份**：建立及還原備份、安排週期性備份，保留遷移或復原所需的存檔資料。
- **工具與稽核**：統一介面查看伺服器輸出、執行支援的操作、檢視稽核歷史，以及一些遊戲小工具。
- **多平台支援**：使用 Windows 可攜式桌面版本、原生 Linux 部署、Docker Compose 或 systemd。

## 安裝

### Windows

從 [Releases](https://github.com/ken1882/palsitter/releases) 下載最新的可攜式壓縮檔，將其
解壓縮到可寫入的目錄後啟動 `Palsitter.exe`。可攜式版本會將設定、設定檔與記錄儲存在本地
`data/` 目錄。

### 原生 Linux

若想要直接在機器上開伺服器需要先裝備對應的 python 環境然後 clone 此專案

接著在專案根目錄執行：

```bash
chmod +x script/linux/palsitter.sh
./script/linux/palsitter.sh install
./script/linux/palsitter.sh run
```

GUI 啟動後，開啟 [http://127.0.0.1:22368/](http://127.0.0.1:22368/)。預設情況下，UI
只監聽 localhost。若要遠端管理，建議使用 SSH 通道：

```bash
ssh -L 22368:127.0.0.1:22368 user@server
```

安裝程式預設使用 `venv`，也支援 `asdf`、`pipenv` 與 `uv`：

```bash
PALSITTER_PYTHON_MANAGER=uv ./script/linux/palsitter.sh install
PALSITTER_PYTHON_MANAGER=uv ./script/linux/palsitter.sh run
```

需要時，可在 `run` 後傳入 `gui.py` 的其他參數：

```bash
./script/linux/palsitter.sh run --host 0.0.0.0 --port 22368
```

請勿在未加入驗證反向代理及適當防火牆規則的情況下，直接將網頁 UI 暴露到公開網際網路。

### Docker

專案包含 Linux 映像檔與 Compose 設定。建置並啟動：

```bash
mkdir -p docker-volumns/config docker-volumns/profile docker-volumns/logs
docker compose up -d --build
```

Compose 使用主機網路，讓每個受管理的 Palworld 執行個體都能接收分配的遊戲、查詢與 REST
連接埠。執行期資料儲存在映像檔之外：

| 主機路徑 | 內容 |
| --- | --- |
| `./docker-volumns/config` | Palsitter 設定 |
| `./docker-volumns/profile` | Palworld 安裝檔、存檔、備份與執行個體資料 |
| `./docker-volumns/logs` | 應用程式記錄 |

容器會以 UID `1000` 執行；必要時，啟動前請讓該使用者可寫入這些 volume 目錄：

```bash
sudo chown -R 1000:1000 docker-volumns
```

在 Docker 主機開啟 [http://127.0.0.1:22368/](http://127.0.0.1:22368/)。若要變更綁定位址
或連接埠，請在 Compose 環境中設定 `PALSITTER_HOST` 或 `PALSITTER_PORT`。

### systemd

先安裝 Python 環境，再為目前的 checkout 安裝並啟動服務：

```bash
./script/linux/palsitter.sh install
sudo ./script/linux/systemd-install.sh
```

查看服務狀態與記錄：

```bash
systemctl status palsitter
journalctl -u palsitter -f
```

## 資料與更新

Linux Shell 部署預設將執行期資料儲存在 `data/`：

```text
data/config/    Palsitter 設定
data/profile/   執行個體、Palworld 安裝檔、存檔與備份
data/logs/      應用程式記錄
```

升級或遷移前，請備份 `data/config` 與 `data/profile`。若要使用其他位置，請在安裝與執行
時一致設定 `PALSITTER_DATA_DIR`：

```bash
export PALSITTER_DATA_DIR=/srv/palsitter-data
./script/linux/palsitter.sh install
./script/linux/palsitter.sh run
```

來源 checkout 的更新方式：

```bash
git pull
./script/linux/palsitter.sh install
./script/linux/palsitter.sh run
```

Docker 部署則透過重新建置映像檔更新：

```bash
docker compose build --pull
docker compose up -d
```

## 文件

- [共用文件](docs/shared/README.md) — 應用程式介面、儲存、語系、檔案瀏覽器與共用 UI 行為。
- [Palworld 文件](docs/games/palworld/README.md) — 概覽、設定、地圖、玩家、模組、存檔、備份、
  連接埠、安裝與生命週期行為。
- [Satisfactory 文件](docs/games/satisfactory/README.md) — 明確的佔位功能契約與限制。
- [完整文件索引](docs/README.md)

## 開發

從 `requirements.txt` 安裝開發相依套件後，執行測試：

```bash
python -m pytest -q
```

專案測試流程：

```bash
python test.py
```

提交變更前，也請執行 `python -m compileall -q .`；修改 GUI 時，請同步更新對應的
Playwright 測試。

## 貢獻與支援

歡迎透過 [GitHub Issues](https://github.com/ken1882/palsitter/issues) 回報錯誤或提出功能建議。
請附上 Palsitter 版本、作業系統、所選遊戲、重現步驟及相關記錄。行為變更的 Pull Request 應包含針對性的測試。

目前的貢獻入口請參考 [Contributing](https://github.com/ken1882/palsitter/contribute)。
