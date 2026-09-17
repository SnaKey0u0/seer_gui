# Seer GUI

Seer GUI 是 Seer 自動化腳本的桌面介面。它會常駐載入一次 PaddleOCR 模型，提供關卡設定檔、具名戰鬥順序、執行 Log 與安全停止控制。

## 專案結構

```text
seer_gui/
  seer_gui.py             GUI 入口、設定檔與戰鬥順序管理
  seer.py                 OCR 與關卡自動化主流程（RunConfig）
  helper/
    battle_control.py     戰鬥點擊、換人與戰鬥序列
    shared_state.py       停止旗標與滑鼠鎖
  configs/                使用者關卡設定檔（執行後自動建立）
  sequences/              使用者戰鬥順序（執行後自動建立）
  assets/                 預留圖示與其他靜態資產
```

`configs/` 與 `sequences/` 是使用者資料，可自由備份或搬移。每次 Start 時，GUI 會將目前表單資料建立為一份不可變的 `RunConfig` 傳給核心，不會再改寫核心或戰鬥控制模組的全域設定。

## 執行

需要python 3.13 (OCR依賴)
```
python -m pip install paddlepaddle-gpu==3.3.1 -i https://www.paddlepaddle.org.cn/packages/stable/cu129/
```
ref: https://www.paddlepaddle.org.cn/install/quick?docurl=/documentation/docs/zh/develop/install/pip/windows-pip.html

請在已安裝既有 Seer 依賴的 Python／conda 環境中執行：

```powershell
python seer_gui.py
```

首次開啟會在背景載入 OCR；完成前 Start 按鈕會停用。按下 Start 後，程式會先等待 3 秒，讓遊戲回到前景，再開始 OCR 掃描。

若環境尚未安裝既有腳本的依賴，先安裝與目前腳本相符的 PaddleOCR、PaddlePaddle、OpenCV、NumPy 與 PyAutoGUI。GPU／CPU 的選擇由根目錄 `automation_settings.toml` 的 `[ocr] device` 控制。

## 固定校正值

`automation_settings.toml` 集中管理螢幕座標、OCR 裝置、等待秒數與拖曳節奏。它不是 GUI 關卡設定，修改後重新啟動 GUI 才會生效。`[flow] ocr_click_delay` 是所有 OCR 目標找到後、點擊前的標準等待；`[battle] click_delay` 是一般點擊後的等待，連點技能則使用 `click_interval_spam`。

## 使用方式

- **關卡配置**：設定 OCR 關鍵字、回合數、是否使用戰鬥順序與連點技能。開始關鍵字一行一筆，順序即關卡執行順序。
- **設定檔**：輸入名稱後儲存；讀取既有設定後，可在名稱欄改名、刪除。程式會自動還原上次成功讀取或儲存的設定檔。
- **戰鬥順序**：每一列是 `skill`、`switch` 或 `swap`。等待秒數留空時使用程式固定的 `3.0` 秒；Note 僅供紀錄，不會傳入自動化腳本。被關卡設定引用的戰鬥順序不可刪除。
- **未儲存提示**：左側分頁名稱與頁面底部會以 `*`／「尚未儲存」提示變更。切換設定、切換戰鬥順序或關閉視窗前，會要求確認是否捨棄未儲存內容。
- **Start / Stop**：執行期間會鎖住設定。Stop 會設定安全停止旗標，待當前 OCR 或滑鼠動作結束後回到等待狀態。把滑鼠移到左上角會觸發 PyAutoGUI failsafe 並安全停止。

若日後要設定 Windows EXE 圖示，請在 `assets/` 放入 `icon.ico`，並在打包指令加入：

```powershell
--icon assets\icon.ico
```
