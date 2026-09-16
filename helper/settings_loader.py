"""讀取根目錄的 automation_settings.toml，供自動化核心共用。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
import tomllib


SETTINGS_PATH = (
    Path(sys.executable).resolve().parent / "automation_settings.toml"
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parents[1] / "automation_settings.toml"
)


@dataclass(frozen=True)
class AutomationSettings:
    ocr_device: str
    ocr_lang: str
    ocr_interval: float
    ocr_click_delay: float
    post_maybe_wait: float
    back_to_menu_pos: tuple[int, int]
    skill_pos: dict[int, tuple[int, int]]
    switch_pos: tuple[int, int]
    swap_pos: dict[int, tuple[int, int]]
    click_delay: float
    drag_up_distance: int
    drag_duration: float
    drag_click_delay: float
    sequence_interval: float
    battle_start_delay: float
    click_interval_spam: float
    continue_timeout: float


def _position(value, label: str) -> tuple[int, int]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{label} 必須是 [x, y]。")
    return int(value[0]), int(value[1])


def _positions(value, label: str) -> dict[int, tuple[int, int]]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} 必須是位置表。")
    return {int(number): _position(position, f"{label}.{number}") for number, position in value.items()}


def load_settings(path: Path = SETTINGS_PATH) -> AutomationSettings:
    try:
        with path.open("rb") as file:
            data = tomllib.load(file)
        ocr, flow, battle = data["ocr"], data["flow"], data["battle"]
        return AutomationSettings(
            ocr_device=str(ocr["device"]),
            ocr_lang=str(ocr["lang"]),
            ocr_interval=float(ocr["interval"]),
            ocr_click_delay=float(flow["ocr_click_delay"]),
            post_maybe_wait=float(flow["post_maybe_wait"]),
            back_to_menu_pos=_position(flow["back_to_menu_pos"], "flow.back_to_menu_pos"),
            skill_pos=_positions(battle["skill_pos"], "battle.skill_pos"),
            switch_pos=_position(battle["switch"]["pos"], "battle.switch.pos"),
            swap_pos=_positions(battle["swap_pos"], "battle.swap_pos"),
            click_delay=float(battle["click_delay"]),
            drag_up_distance=int(battle["drag_up_distance"]),
            drag_duration=float(battle["drag_duration"]),
            drag_click_delay=float(battle["drag_click_delay"]),
            sequence_interval=float(battle["sequence_interval"]),
            battle_start_delay=float(battle["battle_start_delay"]),
            click_interval_spam=float(battle["click_interval_spam"]),
            continue_timeout=float(battle["continue_timeout"]),
        )
    except (FileNotFoundError, KeyError, TypeError, ValueError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeError(f"無法讀取設定檔「{path}」：{exc}") from exc


SETTINGS = load_settings()
