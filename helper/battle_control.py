"""
戰鬥控制
----------------
負責戰鬥中的實際操作：技能點擊、switch、拖移換人上場，以及戰鬥收尾前
「無腦點技能直到出現繼續文字」的操作。

不處理 OCR/畫面辨識——OCR 判斷邏輯全部留在 seer.py，這裡只
負責「點擊/拖移」等實際滑鼠操作，並接受呼叫端傳入的判斷函式
(check_continue)，維持職責分離。

seer.py 會呼叫本檔案提供的高階函式：
    - run_battle_sequence(sequence)  （跑 GUI 傳入的完整戰鬥順序）
    - spam_skill_until_continue(...) （無腦點技能，直到呼叫端判斷為 True）
是否要在戰鬥開始時執行 run_battle_sequence()，由 seer.py 的
RunConfig 決定（本檔案不持有設定資料）。
"""

import time

import pyautogui

import helper.shared_state as shared_state
from helper.settings_loader import SETTINGS


class BattleTimeoutError(Exception):
    """
    spam_skill_until_continue() 超過設定檔的 continue_timeout 秒仍未偵測到繼續文字，
    或 seer.py 的 wait_and_click() 帶 timeout 時逾時未偵測到指定
    關鍵字時拋出，代表畫面可能卡住或出現非預期狀態，需要人工介入處理。
    """

    pass


# ============================================================
# 底層操作
# ============================================================


def click(x, y, loop_click=False):
    with shared_state.click_lock:
        pyautogui.click(x, y)
        if not loop_click:
            shared_state.stop_event.wait(SETTINGS.click_delay)


def drag_up(x, y, distance=None, duration=None):
    """
    從 (x, y) 往上拖移 distance px 後放開。

    這款遊戲需要先點擊該角色一次，讓它進入「可拖移」狀態，才能真正拖移
    （直接 mouseDown -> move -> mouseUp 不會觸發拖移邏輯）。因此流程分兩段：
        0) 先點擊 (x, y) 喚醒角色，等待設定檔的 drag_click_delay 秒
        1) 移到起點停留一下，確保滑鼠真的定位在該處
        2) 按下後停留一下，讓遊戲判定「拾取」動作已成立
        3) 移動到終點後停留一下，確保移動事件已送達，再放開
    """
    distance = SETTINGS.drag_up_distance if distance is None else distance
    duration = SETTINGS.drag_duration if duration is None else duration
    with shared_state.click_lock:
        # 第 0 步：先點擊喚醒，讓角色進入可拖移狀態
        pyautogui.click(x, y)
        time.sleep(SETTINGS.drag_click_delay)

        # 第 1~3 步：正式拖移
        pyautogui.moveTo(x, y)
        time.sleep(SETTINGS.drag_click_delay)
        pyautogui.mouseDown()
        time.sleep(SETTINGS.drag_click_delay)
        pyautogui.moveTo(x, y - distance, duration=duration)
        time.sleep(SETTINGS.drag_click_delay)
        pyautogui.mouseUp()


# ============================================================
# 戰鬥操作序列
# ============================================================


def run_battle_sequence(sequence):
    """
    依序執行 GUI 傳入的戰鬥順序，每個動作之間預設
    等待設定檔的 sequence_interval 秒；skill/switch/swap 可各自帶第三個欄位覆寫
    這個等待時間。stop_event 被觸發時立即中止序列。
    """
    print(f"等待開場動畫（{SETTINGS.battle_start_delay}秒）...")
    if shared_state.stop_event.wait(SETTINGS.battle_start_delay):
        return  # stop_event 在等待期間被觸發

    for step in sequence:
        if shared_state.stop_event.is_set():
            return

        action, n = step[0], step[1]
        wait_override = step[2] if len(step) > 2 else None

        if action == "skill":
            pos = SETTINGS.skill_pos[n]
            print(f"[戰鬥序列] 點擊技能 {n} @ {pos}")
            click(*pos)
        elif action == "switch":
            print(f"[戰鬥序列] 點擊 switch @ {SETTINGS.switch_pos}")
            click(*SETTINGS.switch_pos)
        elif action == "swap":
            pos = SETTINGS.swap_pos[n]
            print(f"[戰鬥序列] 拖移替換角色 {n} @ {pos} (往上 {SETTINGS.drag_up_distance}px)")
            drag_up(*pos)
        else:
            print(f"[戰鬥序列] 未知動作: {action}，跳過。")

        if shared_state.stop_event.is_set():
            return

        wait_time = wait_override if wait_override is not None else SETTINGS.sequence_interval
        shared_state.stop_event.wait(wait_time)


# ============================================================
# 戰鬥收尾前：無腦點技能直到出現繼續文字
# ============================================================


def spam_skill_until_continue(check_continue, skill_n=1, interval=None, timeout=None):
    """
    無腦點擊設定檔中 skill_pos[skill_n]，每次點擊後呼叫呼叫端傳入的 check_continue()
    （通常是一個做 OCR 掃描 + 找關鍵字的函式），用來判斷「繼續文字」是否
    已出現。

    check_continue: 一個不帶參數的函式，回傳 (x, y) 座標或 None。
                     OCR/關鍵字判斷邏輯完全由呼叫端決定，本函式不碰 OCR。
    interval: 每次點擊之間的等待秒數。預設 None 時，於呼叫當下讀取目前的
              設定檔的 click_interval_spam 數值，這樣修改會即時生效，
              不會被函式定義時的預設值卡住。
    timeout: 累計等待秒數超過此值仍未偵測到繼續文字時，拋出
             BattleTimeoutError。預設 None 時，於呼叫當下讀取目前的
             設定檔的 continue_timeout 數值。

    return: check_continue() 回傳的座標，或 stop_event 被觸發時回傳 None。
    raises: BattleTimeoutError -- 超過 timeout 秒仍未偵測到繼續文字。
    """
    if interval is None:
        interval = SETTINGS.click_interval_spam
    if timeout is None:
        timeout = SETTINGS.continue_timeout

    pos = SETTINGS.skill_pos[skill_n]
    start_time = time.monotonic()

    while not shared_state.stop_event.is_set():
        click(*pos, loop_click=True)  # loop_click=True 跳過 click() 內建的 1 秒 sleep
        continue_pos = check_continue()
        if continue_pos:
            return continue_pos

        if time.monotonic() - start_time >= timeout:
            raise BattleTimeoutError(f"超過 {timeout} 秒仍未偵測到繼續文字，判定異常。")

        shared_state.stop_event.wait(interval)
    return None
