"""
主流程
----------------
負責 OCR 掃描、關鍵字偵測與輪數控制；本次執行設定由 GUI 以 RunConfig 傳入。
戰鬥中的實際操作（技能/switch/拖移，以及無腦點技能直到繼續文字出現）
交給 helper/battle_control.py，但 OCR 判斷邏輯留在這裡。
"""

import time
import winsound
from dataclasses import dataclass

import cv2
import numpy as np
import pyautogui
from paddleocr import PaddleOCR

import helper.battle_control as battle_control
import helper.shared_state as shared_state
from helper.settings_loader import SETTINGS


@dataclass(frozen=True)
class RunConfig:
    """由 GUI 建立的一次性執行設定，不保留可被其他執行改寫的全域狀態。"""

    start_keywords: tuple[str, ...]
    pre_keyword: str
    pre2_keyword: str
    post_keyword: str
    post2_keyword: str
    recover_keyword: str
    continue_keyword: str
    max_rounds: int
    use_battle_control: bool
    battle_sequence: tuple[tuple[str, int | None, float | None], ...]
    loop_skill_n: int


pyautogui.FAILSAFE = True


# ============================================================
# OCR
# ============================================================

ocr_engine = PaddleOCR(
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
    lang=SETTINGS.ocr_lang,
    device=SETTINGS.ocr_device,
)


def ocr_scan():
    """
    截圖 + 辨識一次，回傳畫面上所有文字項目。
    一次 scan 的結果可以拿去比對多個關鍵字，不用重複截圖推論。

    return: [(text, x, y), ...]   x, y 為文字框中心座標
    """
    image = np.array(pyautogui.screenshot())
    image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

    result = ocr_engine.predict(image)
    if not result:
        return []

    texts = result[0].get("rec_texts", [])
    boxes = result[0].get("rec_polys", [])

    items = []
    for text, box in zip(texts, boxes):
        points = np.asarray(box).astype(int)
        x = int((points[:, 0].min() + points[:, 0].max()) / 2)
        y = int((points[:, 1].min() + points[:, 1].max()) / 2)
        items.append((text.strip(), x, y))
    return items


def find_keyword(items, keyword):
    """
    在已掃描的結果中找精確符合的關鍵字（可能有多個，例如畫面上同時出現
    多個「開始挑戰」按鈕）。
    keyword = "精靈恢復":
        "精靈恢復"    -> ACCEPT
        "精靈"        -> REJECT
        "恢復"        -> REJECT
        "精靈恢復中"   -> REJECT

    return: [(x, y), ...]，找不到則回傳空 list
    """
    if not keyword:
        return []
    return [(x, y) for text, x, y in items if text == keyword]


def find_keyword_one(items, keyword):
    """
    find_keyword 的單一結果版本，取最後一個符合的座標（原本呼叫端多半只需要
    一個座標，用這個包裝可以不用動到既有呼叫邏輯）。

    return: (x, y) 或 None
    """
    positions = find_keyword(items, keyword)
    return positions[-1] if positions else None


def wait_for_click_ready(label, delay):
    """OCR 找到目標後、點擊前的共用畫面穩定等待。"""
    if delay:
        print(f"找到{label}，等待畫面就緒（{delay:g}秒）...")
        return not shared_state.stop_event.wait(delay)
    return not shared_state.stop_event.is_set()


def wait_and_click(keyword, label, timeout=None, on_timeout="error", click_delay=None):
    """
    掃描並點擊關鍵字。

    timeout=None                    WAIT UNTIL：直到找到或收到 Stop。
    timeout=N, on_timeout="continue" MAYBE WAIT：N 秒未找到即回傳 None。
    timeout=N, on_timeout="error"    N 秒未找到即拋出 BattleTimeoutError。
    click_delay                      OCR 找到後、點擊前的等待秒數；預設為 ocr_click_delay。
    """
    if on_timeout not in {"continue", "error"}:
        raise ValueError("on_timeout 必須是 'continue' 或 'error'。")
    if click_delay is None:
        click_delay = SETTINGS.ocr_click_delay
    deadline = time.monotonic() + timeout if timeout is not None else None

    while not shared_state.stop_event.is_set():
        pos = find_keyword_one(ocr_scan(), keyword)
        if pos:
            if not wait_for_click_ready(label, click_delay):
                return None
            print(f"點擊{label}。")
            battle_control.click(*pos)
            return pos

        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if on_timeout == "continue":
                    print(f"{label} 在 {timeout:g} 秒內未出現，繼續後續流程。")
                    return None
                raise battle_control.BattleTimeoutError(
                    f"超過 {timeout} 秒仍未偵測到「{label}」，判定異常。"
                )
            shared_state.stop_event.wait(min(SETTINGS.ocr_interval, remaining))
        else:
            shared_state.stop_event.wait(SETTINGS.ocr_interval)

    return None


def check_continue(keyword):
    """
    給 battle_control.spam_skill_until_continue() 呼叫的判斷函式：
    掃描畫面，找傳入的繼續關鍵字是否出現。

    return: (x, y) 或 None
    """
    items = ocr_scan()
    return find_keyword_one(items, keyword)


def beep(times=3):
    """
    發出提示音，正常完成、或偵測到異常（如戰鬥超時）時都呼叫這個函式，
    讓使用者能立即察覺並介入處理。
    """
    for _ in range(times):
        winsound.Beep(1000, 300)
        time.sleep(0.15)


def handle_timeout(e):
    """
    統一的逾時/異常處理：印出訊息、提示手動還原畫面、beep、觸發 stop_event。
    給戰鬥收尾前的 CONTINUE 逾時情境使用。
    """
    print(f"\n異常：{e}")
    print("請手動將遊戲畫面還原至乾淨的初始狀態後，再重新執行程式。")
    beep()
    shared_state.stop_event.set()


# ============================================================
# 主流程
# ============================================================

def run_rounds(config: RunConfig):
    total_rounds = 0

    print("掃描開始文字座標...")
    items = ocr_scan()

    # 找出所有開始關鍵字的座標，可能有多個相同文字的按鈕
    stages = []
    for keyword in config.start_keywords:
        positions = find_keyword(items, keyword)
        print(f"找到{len(positions)}個座標: {positions}")
        if len(positions) > 1:
            for pos in positions:
                stages.append((keyword, pos, True))
        else:
            stages.append((keyword, None, False))

    total_target = len(stages) * config.max_rounds

    for keyword, fixed_pos, multi in stages:
        if shared_state.stop_event.is_set():
            break

        for i in range(config.max_rounds):
            if shared_state.stop_event.is_set():
                break

            if multi:
                print(f"\n點擊開始文字...（{keyword} @ {fixed_pos}）")
                battle_control.click(*fixed_pos)
            else:
                print(f"\n等待開始文字...（{keyword}）")
                if wait_and_click(keyword, "開始文字") is None:
                    break  # stop_event 被觸發，中止

            if config.pre_keyword:
                if wait_and_click(config.pre_keyword, "PRE") is None:
                    break  # stop_event 被觸發，中止

            if config.pre2_keyword:
                if wait_and_click(config.pre2_keyword, "PRE2") is None:
                    break  # stop_event 被觸發，中止

            print("開始戰鬥。")

            if config.use_battle_control:
                battle_control.run_battle_sequence(config.battle_sequence)

                if shared_state.stop_event.is_set():
                    break

            # 兩種模式共用：序列結束（或直接跳過序列）後，
            # 無腦點技能1直到畫面出現「點擊任意位置繼續」。
            # 這段期間，下方的 POST/POST2/RECOVER 偵測都還不會執行到，
            # 因為戰鬥根本還沒進入收尾流程。
            try:
                continue_pos = battle_control.spam_skill_until_continue(
                    lambda: check_continue(config.continue_keyword),
                    skill_n=config.loop_skill_n,
                )
            except battle_control.BattleTimeoutError as e:
                handle_timeout(e)
                break

            if continue_pos is None:
                break  # stop_event 被觸發，中止
            if not wait_for_click_ready("繼續文字", SETTINGS.ocr_click_delay):
                break
            print("點擊繼續文字。")
            battle_control.click(*continue_pos)
            if config.post_keyword:
                wait_and_click(
                    config.post_keyword,
                    "POST",
                    timeout=SETTINGS.post_maybe_wait,
                    on_timeout="continue",
                )
            if shared_state.stop_event.is_set():
                break

            if config.post2_keyword:
                wait_and_click(
                    config.post2_keyword,
                    "POST2",
                    timeout=SETTINGS.post_maybe_wait,
                    on_timeout="continue",
                )
            if shared_state.stop_event.is_set():
                break

            while not shared_state.stop_event.is_set():
                pos = find_keyword_one(ocr_scan(), config.recover_keyword)
                if pos:
                    if not wait_for_click_ready("恢復文字", SETTINGS.ocr_click_delay):
                        break
                    print("點擊恢復文字。")
                    battle_control.click(*pos)
                    break
                shared_state.stop_event.wait(SETTINGS.ocr_interval)

            total_rounds += 1
            stage_label = f"{keyword} @ {fixed_pos}" if multi else keyword
            print(f"完成「{stage_label}」第 {i + 1}/{config.max_rounds} 輪（總計 {total_rounds}/{total_target}）")

            if config.pre_keyword or config.pre2_keyword:
                battle_control.click(*SETTINGS.back_to_menu_pos)

        if shared_state.stop_event.is_set():
            break

    if total_rounds >= total_target:
        print("\n所有輪數完成!")
        beep()


if __name__ == "__main__":
    print("請使用 python seer_gui.py 啟動 Seer GUI。")
