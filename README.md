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

## 打包 Windows EXE

請在已可成功執行 `python seer_gui.py` 的同一個環境中執行：

```powershell
python -m pip install pyinstaller

python -m PyInstaller --noconfirm --clean --onedir --windowed --name SeerGUI --hidden-import seer --hidden-import helper.battle_control --hidden-import helper.shared_state --collect-all paddleocr --collect-all paddle --collect-all paddlex seer_gui.py
```

產物位於 `dist\SeerGUI\SeerGUI.exe`。使用 `--onedir` 是刻意設計：PaddleOCR 及其模型依賴較大，資料夾模式較可靠，也避免單檔模式每次啟動解壓縮。`seer` 是 GUI 在背景用動態 import 載入的核心，故指令中的三個 `--hidden-import` 不可省略。

首次打包後請先測試 EXE 是否能載入 OCR 模型。若要沿用既有資料，將專案的 `configs/`、`sequences/` 複製到 `SeerGUI.exe` 同層；程式也會在該處自動建立它們。

不要只移動 `SeerGUI.exe`。one-folder 模式要求 `_internal/` 與 EXE 保持同層；若要把程式移到專案根目錄，請一起移動兩者，最終結構如下：

```text
seer_gui/
  SeerGUI.exe
  _internal/
  configs/
  sequences/
```

PaddleOCR 的模型快取位置會隨版本與環境而異。若 EXE 必須在沒有模型快取或沒有網路的電腦上離線使用，先確認該環境的模型快取位置，再將模型資產一併納入打包流程；不要直接猜測快取路徑。

若日後要設定 Windows EXE 圖示，請在 `assets/` 放入 `icon.ico`，並在打包指令加入：

```powershell
--icon assets\icon.ico
```
