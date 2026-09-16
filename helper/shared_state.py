"""
共用非同步狀態
----------------
seer.py 和 helper/battle_control.py 都需要用到同一份滑鼠鎖和
中止旗標，獨立成這個檔案避免兩邊互相 import 造成循環依賴。
"""

import threading

click_lock = threading.Lock()        # 滑鼠操作共用鎖，避免不同來源同時搶著移動滑鼠
stop_event = threading.Event()       # 收到中止訊號（Ctrl+C / pyautogui failsafe / 例外）
