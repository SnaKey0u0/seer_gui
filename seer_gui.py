"""Seer GUI：設定檔、戰鬥順序與既有自動化流程的桌面入口。"""

from __future__ import annotations

import contextlib
import importlib
import json
import queue
import re
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk


# 打包後使用 exe 同層，開發時使用本檔案同層；設定檔永遠是使用者可見資料。
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
CONFIG_DIR = APP_DIR / "configs"
SEQUENCE_DIR = APP_DIR / "sequences"
LAST_USED_PATH = APP_DIR / "last_used.json"
INVALID_FILENAME = re.compile(r'[<>:"/\\|?*]')
SWITCH_NOTE = "switch後面接的swap需3秒"

DEFAULT_SETTINGS = {
    "start_keywords": ["挑戰擊敗"],
    "pre_keyword": "",
    "pre2_keyword": "",
    "post_keyword": "",
    "post2_keyword": "",
    "recover_keyword": "精靈恢復",
    "continue_keyword": "點擊任意位置繼續",
    "max_rounds": 20,
    "use_battle_control": False,
    "sequence_name": "星巴克",
    "loop_skill_n": 3,
}

DEFAULT_SEQUENCES = {
    "含草神": [
        ["skill", 1, None, ""], ["swap", 2, 0.5, ""], ["skill", 1, None, ""],
        ["swap", 3, 0.5, ""], ["skill", 2, 4.0, ""], ["switch", None, 0.5, SWITCH_NOTE],
        ["swap", 5, None, ""], ["swap", 4, 0.5, ""],
    ],
    "星巴克": [
        ["skill", 1, None, ""], ["swap", 2, 0.5, ""], ["skill", 2, 4.0, ""],
        ["switch", None, 0.5, SWITCH_NOTE], ["swap", 3, None, ""], ["swap", 4, 0.5, ""],
    ],
}


class PlaceholderEntry(tk.Entry):
    def __init__(self, master, placeholder: str, **kwargs):
        super().__init__(master, **kwargs)
        self.placeholder = placeholder
        self.placeholder_active = False
        self.bind("<FocusIn>", self._remove_placeholder)
        self.bind("<FocusOut>", self._show_placeholder)
        self._show_placeholder()

    def _show_placeholder(self, _event=None):
        if not self.get():
            self.insert(0, self.placeholder)
            self.configure(fg="#8a8a8a")
            self.placeholder_active = True

    def _remove_placeholder(self, _event=None):
        if self.placeholder_active:
            self.delete(0, tk.END)
            self.configure(fg="#f2f2f2")
            self.placeholder_active = False

    def value(self) -> str:
        return "" if self.placeholder_active else self.get().strip()

    def set_value(self, value: float | None):
        self._remove_placeholder()
        self.delete(0, tk.END)
        if value is None:
            self._show_placeholder()
        else:
            self.insert(0, str(value))


class QueueWriter:
    """將既有腳本的 print 輸出逐行送進 Tk 主執行緒的 queue。"""

    def __init__(self, target: queue.Queue[str]):
        self.target = target
        self.partial = ""

    def write(self, text: str) -> int:
        self.partial += text
        lines = self.partial.split("\n")
        self.partial = lines.pop()
        for line in lines:
            if line:
                self.target.put(line)
        return len(text)

    def flush(self) -> None:
        if self.partial:
            self.target.put(self.partial)
            self.partial = ""


class SeerGui(tk.Tk):
    def __init__(self):
        super().__init__()
        CONFIG_DIR.mkdir(exist_ok=True)
        SEQUENCE_DIR.mkdir(exist_ok=True)
        self.title("Seer GUI")
        self.geometry("1180x700")
        self.minsize(1050, 620)
        self.configure(bg="#111111")

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.sequence_data: dict[str, list[list[Any]]] = {}
        self.current_sequence_name = tk.StringVar()
        self.active_sequence_name = ""
        self.profile_snapshot: str | None = None
        self.sequence_snapshots: dict[str, str] = {}
        self.suspend_dirty_tracking = False
        self.backend_ready = False
        self.running = False
        self.seer: Any | None = None
        self.battle_control: Any | None = None
        self.editable_widgets: list[tk.Widget] = []

        self._setup_style()
        self._load_sequences()
        self._build_layout()
        self._refresh_profile_names()
        self._sync_sequence_choices()
        self._restore_last_profile()
        if self.profile_snapshot is None:
            self._mark_profile_saved()
        self._refresh_dirty_ui()
        self._show_page("config")
        self.after(150, self._drain_log_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.start_button.configure(state="disabled")
        self._log("正在載入 OCR 模型，完成前不能開始…")
        threading.Thread(target=self._load_backend, daemon=True).start()

    def _setup_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TCombobox", fieldbackground="#1a1a1a", background="#1a1a1a", foreground="#f2f2f2", arrowcolor="#f2f2f2")
        style.map("TCombobox", fieldbackground=[("readonly", "#1a1a1a"), ("disabled", "#1a1a1a")], foreground=[("readonly", "#f2f2f2"), ("disabled", "#7f7f7f")])
        style.configure("Treeview", background="#161616", fieldbackground="#161616", foreground="#f2f2f2", rowheight=28, bordercolor="#777777")
        style.configure("Treeview.Heading", background="#282828", foreground="#f2f2f2", relief="flat")
        style.map("Treeview", background=[("selected", "#365f91")])
        self.option_add("*TCombobox*Listbox.background", "#1a1a1a")
        self.option_add("*TCombobox*Listbox.foreground", "#f2f2f2")
        self.option_add("*TCombobox*Listbox.selectBackground", "#365f91")

    def _button(self, parent, text, command, **kwargs):
        return tk.Button(parent, text=text, command=command, bg="#171717", fg="#f5f5f5", disabledforeground="#7f7f7f", activebackground="#323232", activeforeground="#ffffff", relief="solid", bd=1, font=("Microsoft JhengHei UI", 10), **kwargs)

    def _label(self, parent, text, **kwargs):
        return tk.Label(parent, text=text, bg="#111111", fg="#f1f1f1", font=("Microsoft JhengHei UI", 10), **kwargs)

    def _entry(self, parent, variable=None, **kwargs):
        return tk.Entry(parent, textvariable=variable, bg="#1a1a1a", fg="#f2f2f2", disabledbackground="#1a1a1a", disabledforeground="#7f7f7f", insertbackground="#f2f2f2", relief="solid", bd=1, font=("Microsoft JhengHei UI", 10), **kwargs)

    def _track(self, widget):
        self.editable_widgets.append(widget)
        return widget

    def _build_layout(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(2, weight=1)
        self.grid_rowconfigure(0, weight=1)

        nav = tk.Frame(self, bg="#111111", highlightbackground="#d0d0d0", highlightthickness=1, width=145)
        nav.grid(row=0, column=0, sticky="nsew", padx=(10, 8), pady=10)
        nav.grid_propagate(False)
        self.page_buttons = {
            "config": self._button(nav, "關卡配置", lambda: self._show_page("config"), height=2),
            "battle": self._button(nav, "戰鬥順序", lambda: self._show_page("battle"), height=2),
        }
        self.page_buttons["config"].pack(fill="x", padx=10, pady=(10, 8))
        self.page_buttons["battle"].pack(fill="x", padx=10)
        controls = tk.Frame(nav, bg="#111111")
        controls.pack(side="bottom", fill="x", padx=10, pady=10)
        self.start_button = self._button(controls, "Start", self._start, height=2)
        self.stop_button = self._button(controls, "Stop", self._stop, height=2, state="disabled")
        self.start_button.pack(fill="x", pady=(0, 14))
        self.stop_button.pack(fill="x")

        content = tk.Frame(self, bg="#111111", highlightbackground="#d0d0d0", highlightthickness=1)
        content.grid(row=0, column=1, sticky="nsew", padx=(0, 18), pady=10)
        content.grid_rowconfigure(0, weight=1)
        content.grid_columnconfigure(0, weight=1)
        self.config_page = tk.Frame(content, bg="#111111")
        self.battle_page = tk.Frame(content, bg="#111111")
        for page in (self.config_page, self.battle_page):
            page.grid(row=0, column=0, sticky="nsew", padx=26, pady=22)
        self._build_config_page()
        self._build_battle_page()

        log_frame = tk.Frame(self, bg="#111111", highlightbackground="#d0d0d0", highlightthickness=1, width=305)
        log_frame.grid(row=0, column=2, sticky="nsew", padx=(0, 10), pady=10)
        log_frame.grid_propagate(False)
        self._label(log_frame, "執行 Log", anchor="w").pack(fill="x", padx=12, pady=(10, 5))
        log_container = tk.Frame(log_frame, bg="#111111")
        log_container.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.log_text = tk.Text(log_container, bg="#151515", fg="#e7e7e7", insertbackground="#f2f2f2", wrap="word", relief="solid", bd=1, state="disabled", font=("Consolas", 9))
        log_scroll = ttk.Scrollbar(log_container, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        self.profile_status = tk.Label(self.config_page, bg="#111111", fg="#f0b848", anchor="e", font=("Microsoft JhengHei UI", 9))
        self.profile_status.grid(row=99, column=0, columnspan=2, sticky="e", pady=(12, 0))
        self.sequence_status = tk.Label(self.battle_page, bg="#111111", fg="#f0b848", anchor="e", font=("Microsoft JhengHei UI", 9))
        self.sequence_status.grid(row=99, column=0, sticky="e", pady=(12, 0))

    def _build_config_page(self):
        self.config_page.grid_columnconfigure(1, weight=1)
        self.profile_name_var = tk.StringVar()
        self.profile_choice_var = tk.StringVar()
        self.settings_vars = {key: tk.StringVar() for key in ("pre_keyword", "pre2_keyword", "post_keyword", "post2_keyword", "recover_keyword", "continue_keyword", "sequence_name")}
        self.rounds_var = tk.StringVar(value="20")
        self.use_battle_var = tk.BooleanVar(value=False)
        self.loop_skill_var = tk.StringVar(value="3")

        def add_row(row, label, widget):
            self._label(self.config_page, label, anchor="e").grid(row=row, column=0, sticky="ne", padx=(0, 14), pady=6)
            widget.grid(row=row, column=1, sticky="ew", pady=6)
            self._track(widget)

        add_row(0, "名稱", self._entry(self.config_page, self.profile_name_var))
        self.start_keywords_text = tk.Text(self.config_page, height=4, bg="#1a1a1a", fg="#f2f2f2", insertbackground="#f2f2f2", relief="solid", bd=1, font=("Microsoft JhengHei UI", 10))
        add_row(1, "開始關鍵字\n（一行一個）", self.start_keywords_text)
        row = 2
        for key, label in (("pre_keyword", "PRE 關鍵字"), ("pre2_keyword", "PRE2 關鍵字"), ("post_keyword", "POST 關鍵字"), ("post2_keyword", "POST2 關鍵字"), ("recover_keyword", "恢復關鍵字"), ("continue_keyword", "繼續關鍵字")):
            add_row(row, label, self._entry(self.config_page, self.settings_vars[key]))
            row += 1
        add_row(row, "最大回合數", self._entry(self.config_page, self.rounds_var)); row += 1
        check = tk.Checkbutton(self.config_page, text="使用戰鬥順序", variable=self.use_battle_var, bg="#111111", fg="#f2f2f2", disabledforeground="#7f7f7f", activebackground="#111111", activeforeground="#f2f2f2", selectcolor="#1a1a1a", command=self._on_profile_edited)
        add_row(row, "戰鬥模式", check); row += 1
        self.profile_sequence_combo = ttk.Combobox(self.config_page, textvariable=self.settings_vars["sequence_name"], state="readonly")
        add_row(row, "戰鬥順序", self.profile_sequence_combo); row += 1
        loop_combo = ttk.Combobox(self.config_page, textvariable=self.loop_skill_var, values=("1", "2", "3", "4", "5"), state="readonly")
        add_row(row, "連點技能", loop_combo); row += 1

        actions = tk.Frame(self.config_page, bg="#111111")
        actions.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(18, 0))
        actions.grid_columnconfigure((0, 1, 2, 3, 4), weight=1)
        self._track(self._button(actions, "儲存設定", self._save_profile)).grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self._track(self._button(actions, "修改名稱", self._rename_profile)).grid(row=0, column=1, sticky="ew", padx=5)
        self.profile_combo = self._track(ttk.Combobox(actions, textvariable=self.profile_choice_var, state="readonly"))
        self.profile_combo.grid(row=0, column=2, sticky="ew", padx=5)
        self._track(self._button(actions, "讀取設定", self._load_profile)).grid(row=0, column=3, sticky="ew", padx=(5, 0))
        self._track(self._button(actions, "刪除設定", self._delete_profile)).grid(row=0, column=4, sticky="ew", padx=(5, 0))
        self._set_settings(DEFAULT_SETTINGS)
        for var in (*self.settings_vars.values(), self.profile_name_var, self.rounds_var, self.loop_skill_var):
            var.trace_add("write", self._on_profile_edited)
        self.start_keywords_text.bind("<<Modified>>", self._on_start_keywords_modified)

    def _build_battle_page(self):
        self.battle_page.grid_columnconfigure(0, weight=1)
        self.battle_page.grid_rowconfigure(1, weight=1)
        top = tk.Frame(self.battle_page, bg="#111111")
        top.grid(row=0, column=0, sticky="ew")
        top.grid_columnconfigure(1, weight=1)
        self._label(top, "戰鬥順序名稱").grid(row=0, column=0, padx=(0, 10))
        self.sequence_combo = self._track(ttk.Combobox(top, textvariable=self.current_sequence_name, state="readonly"))
        self.sequence_combo.grid(row=0, column=1, sticky="ew")
        self.sequence_combo.bind("<<ComboboxSelected>>", self._on_sequence_selected)
        for column, text, command in ((2, "新增", self._new_sequence), (3, "修改名稱", self._rename_sequence), (4, "儲存順序", self._save_sequence), (5, "刪除順序", self._delete_sequence)):
            self._track(self._button(top, text, command)).grid(row=0, column=column, padx=(8, 0))

        table_frame = tk.Frame(self.battle_page, bg="#111111")
        table_frame.grid(row=1, column=0, sticky="nsew", pady=(16, 12))
        self.steps = self._track(ttk.Treeview(table_frame, columns=("no", "action", "position", "wait", "note"), show="headings", selectmode="browse"))
        for col, title, width in (("no", "#", 45), ("action", "動作", 100), ("position", "點擊位置", 90), ("wait", "等待秒數", 100), ("note", "Note", 190)):
            self.steps.heading(col, text=title)
            self.steps.column(col, width=width, anchor="center")
        scroll = ttk.Scrollbar(table_frame, command=self.steps.yview)
        self.steps.configure(yscrollcommand=scroll.set)
        self.steps.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.steps.bind("<<TreeviewSelect>>", self._on_step_selected)

        editor = tk.Frame(self.battle_page, bg="#111111")
        editor.grid(row=2, column=0, sticky="ew")
        self.action_var, self.position_var, self.note_var = tk.StringVar(value="skill"), tk.StringVar(value="1"), tk.StringVar()
        self._label(editor, "動作").grid(row=0, column=0, padx=(0, 6))
        self.action_combo = self._track(ttk.Combobox(editor, textvariable=self.action_var, values=("skill", "switch", "swap"), state="readonly", width=10))
        self.action_combo.grid(row=0, column=1, padx=(0, 12))
        self.action_combo.bind("<<ComboboxSelected>>", lambda _event: self._sync_step_editor())
        self._label(editor, "點擊位置").grid(row=0, column=2, padx=(0, 6))
        self.position_combo = self._track(ttk.Combobox(editor, textvariable=self.position_var, state="readonly", width=6))
        self.position_combo.grid(row=0, column=3, padx=(0, 12))
        self._label(editor, "等待秒數").grid(row=0, column=4, padx=(0, 6))
        self.wait_entry = self._track(PlaceholderEntry(editor, "3.0", bg="#1a1a1a", fg="#f2f2f2", disabledbackground="#1a1a1a", disabledforeground="#7f7f7f", insertbackground="#f2f2f2", relief="solid", bd=1, width=10))
        self.wait_entry.grid(row=0, column=5, padx=(0, 12))
        self._label(editor, "Note").grid(row=1, column=0, padx=(0, 6), pady=(10, 0))
        self.note_entry = self._track(self._entry(editor, self.note_var))
        self.note_entry.grid(row=1, column=1, columnspan=5, sticky="ew", padx=(0, 12), pady=(10, 0))
        self._track(self._button(editor, "新增步驟", self._add_step)).grid(row=0, column=6, rowspan=2, padx=3, sticky="ns")
        self._track(self._button(editor, "更新", self._update_step)).grid(row=0, column=7, rowspan=2, padx=3, sticky="ns")
        controls = tk.Frame(self.battle_page, bg="#111111")
        controls.grid(row=3, column=0, sticky="e", pady=(12, 0))
        for text, command in (("刪除", self._delete_step), ("上移", lambda: self._move_step(-1)), ("下移", lambda: self._move_step(1))):
            self._track(self._button(controls, text, command)).pack(side="left", padx=3)
        self._sync_step_editor()

    def _show_page(self, page: str):
        (self.config_page if page == "config" else self.battle_page).tkraise()
        for key, button in self.page_buttons.items():
            button.configure(bg="#343434" if key == page else "#171717")

    def _safe_name(self, name: str) -> str:
        name = name.strip()
        if not name or INVALID_FILENAME.search(name) or name in {".", ".."}:
            raise ValueError("名稱不可為空白或含有 \\ / : * ? \" < > |。")
        return name

    def _write_json(self, path: Path, data: dict[str, Any]):
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _validate_settings(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValueError("設定內容不是物件。")
        settings = DEFAULT_SETTINGS | raw
        keywords = settings["start_keywords"]
        if not isinstance(keywords, list) or not (keywords := [str(item).strip() for item in keywords if str(item).strip()]):
            raise ValueError("至少要有一個開始關鍵字。")
        try:
            rounds, loop_skill = int(settings["max_rounds"]), int(settings["loop_skill_n"])
        except (TypeError, ValueError) as exc:
            raise ValueError("最大回合數與連點技能必須是整數。") from exc
        if rounds < 1 or loop_skill not in range(1, 6):
            raise ValueError("最大回合數須大於 0，連點技能須為 1–5。")
        if not isinstance(settings["use_battle_control"], bool):
            raise ValueError("use_battle_control 必須是 true 或 false。")
        for key in ("pre_keyword", "pre2_keyword", "post_keyword", "post2_keyword", "recover_keyword", "continue_keyword", "sequence_name"):
            if not isinstance(settings[key], str):
                raise ValueError(f"{key} 必須是文字。")
        return {**settings, "start_keywords": keywords, "max_rounds": rounds, "loop_skill_n": loop_skill}

    def _validate_steps(self, raw: Any, sequence_name: str) -> list[list[Any]]:
        if not isinstance(raw, list):
            raise ValueError(f"戰鬥順序「{sequence_name}」必須是步驟列表。")
        normalised = []
        for index, step in enumerate(raw, start=1):
            if not isinstance(step, (list, tuple)) or len(step) not in (3, 4):
                raise ValueError(f"戰鬥順序「{sequence_name}」第 {index} 步格式錯誤。")
            action, position, wait = step[:3]
            if action not in {"skill", "switch", "swap"}:
                raise ValueError(f"戰鬥順序「{sequence_name}」第 {index} 步動作無效。")
            if action == "switch":
                position = None
            else:
                try:
                    position = int(position)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"戰鬥順序「{sequence_name}」第 {index} 步點擊位置無效。") from exc
                max_position = 5 if action == "skill" else 6
                if position not in range(1, max_position + 1):
                    raise ValueError(f"戰鬥順序「{sequence_name}」第 {index} 步點擊位置超出範圍。")
            if wait is not None:
                try:
                    wait = float(wait)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"戰鬥順序「{sequence_name}」第 {index} 步等待秒數無效。") from exc
                if wait < 0:
                    raise ValueError(f"戰鬥順序「{sequence_name}」第 {index} 步等待秒數不可小於 0。")
            note = str(step[3]).strip() if len(step) == 4 else ""
            normalised.append([action, position, wait, note or (SWITCH_NOTE if action == "switch" else "")])
        return normalised

    def _config_from_form(self) -> dict[str, Any]:
        raw = {
            "start_keywords": self.start_keywords_text.get("1.0", tk.END).splitlines(),
            "pre_keyword": self.settings_vars["pre_keyword"].get().strip(),
            "pre2_keyword": self.settings_vars["pre2_keyword"].get().strip(),
            "post_keyword": self.settings_vars["post_keyword"].get().strip(),
            "post2_keyword": self.settings_vars["post2_keyword"].get().strip(),
            "recover_keyword": self.settings_vars["recover_keyword"].get().strip(),
            "continue_keyword": self.settings_vars["continue_keyword"].get().strip(),
            "max_rounds": self.rounds_var.get(),
            "use_battle_control": self.use_battle_var.get(),
            "sequence_name": self.settings_vars["sequence_name"].get(),
            "loop_skill_n": self.loop_skill_var.get(),
        }
        return self._validate_settings(raw)

    def _set_settings(self, settings: dict[str, Any]):
        settings = self._validate_settings(settings)
        self.start_keywords_text.delete("1.0", tk.END)
        self.start_keywords_text.insert("1.0", "\n".join(settings["start_keywords"]))
        for key, var in self.settings_vars.items():
            var.set(settings[key])
        self.rounds_var.set(str(settings["max_rounds"]))
        self.use_battle_var.set(settings["use_battle_control"])
        self.loop_skill_var.set(str(settings["loop_skill_n"]))
        self._update_battle_control_state()

    # ------------------------------------------------------------------
    # 未儲存狀態：以可序列化快照比較，而非在每個 UI 操作中維護旗標。

    @staticmethod
    def _snapshot(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _profile_form_state(self) -> dict[str, Any]:
        return {
            "name": self.profile_name_var.get(),
            "settings": {
                "start_keywords": self.start_keywords_text.get("1.0", "end-1c").splitlines(),
                "pre_keyword": self.settings_vars["pre_keyword"].get(),
                "pre2_keyword": self.settings_vars["pre2_keyword"].get(),
                "post_keyword": self.settings_vars["post_keyword"].get(),
                "post2_keyword": self.settings_vars["post2_keyword"].get(),
                "recover_keyword": self.settings_vars["recover_keyword"].get(),
                "continue_keyword": self.settings_vars["continue_keyword"].get(),
                "max_rounds": self.rounds_var.get(),
                "use_battle_control": self.use_battle_var.get(),
                "sequence_name": self.settings_vars["sequence_name"].get(),
                "loop_skill_n": self.loop_skill_var.get(),
            },
        }

    def _mark_profile_saved(self):
        self.profile_snapshot = self._snapshot(self._profile_form_state())
        self._refresh_dirty_ui()

    def _mark_sequence_saved(self, name: str):
        self.sequence_snapshots[name] = self._snapshot(self.sequence_data[name])
        self._refresh_dirty_ui()

    def _is_profile_dirty(self) -> bool:
        return self.profile_snapshot is None or self.profile_snapshot != self._snapshot(self._profile_form_state())

    def _is_sequence_dirty(self, name: str | None = None) -> bool:
        name = name or self.active_sequence_name
        return not name or name not in self.sequence_snapshots or self.sequence_snapshots[name] != self._snapshot(self.sequence_data[name])

    def _refresh_dirty_ui(self):
        if not hasattr(self, "profile_status"):
            return
        profile_dirty = self._is_profile_dirty()
        sequence_dirty = self._is_sequence_dirty()
        self.page_buttons["config"].configure(text="關卡配置 *" if profile_dirty else "關卡配置")
        self.page_buttons["battle"].configure(text="戰鬥順序 *" if sequence_dirty else "戰鬥順序")
        self.profile_status.configure(text="● 尚未儲存" if profile_dirty else "")
        self.sequence_status.configure(text="● 尚未儲存" if sequence_dirty else "")

    def _on_profile_edited(self, *_args):
        if not self.suspend_dirty_tracking:
            self._update_battle_control_state()
            self._refresh_dirty_ui()

    def _on_start_keywords_modified(self, _event=None):
        self.start_keywords_text.edit_modified(False)
        self._on_profile_edited()

    def _confirm_discard_profile(self) -> bool:
        return not self._is_profile_dirty() or messagebox.askyesno(
            "尚未儲存", "目前關卡配置有未儲存變更。\n確定要捨棄這些變更嗎？"
        )

    def _confirm_discard_sequence(self, name: str) -> bool:
        return not self._is_sequence_dirty(name) or messagebox.askyesno(
            "尚未儲存", f"戰鬥順序「{name}」有未儲存變更。\n確定要捨棄這些變更嗎？"
        )

    def _refresh_profile_names(self):
        self.profile_combo.configure(values=sorted(path.stem for path in CONFIG_DIR.glob("*.json")))

    def _remember_profile(self, name: str):
        try:
            self._write_json(LAST_USED_PATH, {"last_profile": name})
        except OSError:
            # 最近使用記錄失敗不應影響主要設定檔操作。
            pass

    def _restore_last_profile(self):
        try:
            data = json.loads(LAST_USED_PATH.read_text(encoding="utf-8"))
            name = self._safe_name(data["last_profile"])
            if not (CONFIG_DIR / f"{name}.json").exists():
                return
            self.profile_choice_var.set(name)
            if not self._load_profile(skip_discard_check=True, remember=False, silent=True):
                self.profile_choice_var.set("")
        except (OSError, KeyError, TypeError, json.JSONDecodeError, ValueError):
            # 記錄檔損毀或指向不存在設定時靜默使用預設值。
            return

    def _clear_last_profile_if_matches(self, name: str):
        try:
            data = json.loads(LAST_USED_PATH.read_text(encoding="utf-8"))
            if data.get("last_profile") == name:
                LAST_USED_PATH.unlink(missing_ok=True)
        except (OSError, json.JSONDecodeError):
            pass

    def _save_profile(self):
        try:
            name = self._safe_name(self.profile_name_var.get())
            self.suspend_dirty_tracking = True
            self.profile_name_var.set(name)
            self.suspend_dirty_tracking = False
            target = CONFIG_DIR / f"{name}.json"
            if target.exists() and not messagebox.askyesno("確認覆蓋", f"「{name}」已存在，是否覆蓋？"):
                return
            self._write_json(target, {"name": name, "settings": self._config_from_form()})
        except (OSError, ValueError) as exc:
            self.suspend_dirty_tracking = False
            messagebox.showerror("無法儲存", str(exc))
            return
        self.profile_choice_var.set(name)
        self._refresh_profile_names()
        self._mark_profile_saved()
        self._remember_profile(name)
        self._log(f"已儲存關卡設定：{name}")

    def _load_profile(self, skip_discard_check=False, remember=True, silent=False):
        name = self.profile_choice_var.get()
        if not name:
            messagebox.showinfo("讀取設定", "請先選擇一個設定檔。")
            return False
        if not skip_discard_check and not self._confirm_discard_profile():
            return False
        try:
            data = json.loads((CONFIG_DIR / f"{name}.json").read_text(encoding="utf-8"))
            settings = self._validate_settings(data["settings"])
            if settings["sequence_name"] not in self.sequence_data:
                raise ValueError(f"設定引用的戰鬥順序「{settings['sequence_name']}」不存在。")
            self.suspend_dirty_tracking = True
            self.profile_name_var.set(self._safe_name(data["name"]))
            self._set_settings(settings)
            self.suspend_dirty_tracking = False
        except (OSError, KeyError, json.JSONDecodeError, ValueError) as exc:
            self.suspend_dirty_tracking = False
            if not silent:
                messagebox.showerror("無法讀取", f"設定檔「{name}」有問題：{exc}")
            return False
        self._mark_profile_saved()
        if remember:
            self._remember_profile(name)
        self._log(f"已讀取關卡設定：{name}")
        return True

    def _rename_profile(self):
        old_name = self.profile_choice_var.get()
        if not old_name:
            messagebox.showinfo("修改名稱", "請先從下拉選單讀取要修改的關卡設定。")
            return
        try:
            new_name = self._safe_name(self.profile_name_var.get())
            self.suspend_dirty_tracking = True
            self.profile_name_var.set(new_name)
            self.suspend_dirty_tracking = False
            old_path, new_path = CONFIG_DIR / f"{old_name}.json", CONFIG_DIR / f"{new_name}.json"
            if new_name == old_name:
                return
            if new_path.exists():
                raise ValueError("已有同名設定檔。")
            data = json.loads(old_path.read_text(encoding="utf-8"))
            data["name"] = new_name
            self._write_json(new_path, data)
            old_path.unlink()
        except (OSError, KeyError, json.JSONDecodeError, ValueError) as exc:
            self.suspend_dirty_tracking = False
            messagebox.showerror("無法修改名稱", str(exc))
            return
        self.profile_choice_var.set(new_name)
        self._refresh_profile_names()
        self._mark_profile_saved()
        self._remember_profile(new_name)
        self._log(f"關卡設定已改名：{old_name} → {new_name}")

    def _delete_profile(self):
        name = self.profile_choice_var.get()
        if not name:
            messagebox.showinfo("刪除設定", "請先選擇一個設定檔。")
            return
        if not messagebox.askyesno("確認刪除", f"確定要刪除關卡設定「{name}」嗎？\n此操作無法復原。"):
            return
        try:
            (CONFIG_DIR / f"{name}.json").unlink()
        except OSError as exc:
            messagebox.showerror("無法刪除", str(exc))
            return
        self.profile_choice_var.set("")
        self._refresh_profile_names()
        self._clear_last_profile_if_matches(name)
        self.suspend_dirty_tracking = True
        self.profile_name_var.set("")
        self._set_settings(DEFAULT_SETTINGS)
        self.suspend_dirty_tracking = False
        self._mark_profile_saved()
        self._log(f"已刪除關卡設定：{name}")

    def _load_sequences(self):
        for path in SEQUENCE_DIR.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                name = self._safe_name(data["name"])
                self.sequence_data[name] = self._validate_steps(data["steps"], name)
            except (OSError, KeyError, json.JSONDecodeError, ValueError) as exc:
                self._log(f"略過無效戰鬥順序「{path.name}」：{exc}")
        if not self.sequence_data:
            self.sequence_data = {name: [step.copy() for step in steps] for name, steps in DEFAULT_SEQUENCES.items()}
        self.sequence_snapshots = {name: self._snapshot(steps) for name, steps in self.sequence_data.items()}
        self.current_sequence_name.set(next(iter(sorted(self.sequence_data)), ""))
        self.active_sequence_name = self.current_sequence_name.get()

    def _sync_sequence_choices(self):
        names = sorted(self.sequence_data)
        self.sequence_combo.configure(values=names)
        self.profile_sequence_combo.configure(values=names)
        if self.current_sequence_name.get() not in names:
            self.current_sequence_name.set(names[0] if names else "")
        if self.settings_vars["sequence_name"].get() not in names:
            self.settings_vars["sequence_name"].set(names[0] if names else "")
        self._refresh_steps()

    def _new_sequence(self):
        name = simpledialog.askstring("新增戰鬥順序", "請輸入新順序名稱：", parent=self)
        if name is None:
            return
        try:
            name = self._safe_name(name)
            if name in self.sequence_data:
                raise ValueError("此戰鬥順序已存在。")
        except ValueError as exc:
            messagebox.showerror("名稱無效", str(exc))
            return
        self.sequence_data[name] = []
        self.sequence_snapshots.pop(name, None)
        self.current_sequence_name.set(name)
        self.active_sequence_name = name
        self._sync_sequence_choices()
        self._refresh_dirty_ui()
        self._log(f"已新增尚未儲存的戰鬥順序：{name}")

    def _rename_sequence(self):
        old_name = self.current_sequence_name.get()
        if not old_name:
            return
        new_name = simpledialog.askstring("修改戰鬥順序名稱", "請輸入新名稱：", initialvalue=old_name, parent=self)
        if new_name is None:
            return
        try:
            new_name = self._safe_name(new_name)
            if new_name == old_name:
                return
            if new_name in self.sequence_data or (SEQUENCE_DIR / f"{new_name}.json").exists():
                raise ValueError("此戰鬥順序已存在。")
            old_path, new_path = SEQUENCE_DIR / f"{old_name}.json", SEQUENCE_DIR / f"{new_name}.json"
            if old_path.exists():
                data = json.loads(old_path.read_text(encoding="utf-8"))
                data["name"] = new_name
                self._write_json(new_path, data)
                old_path.unlink()
            for config_path in CONFIG_DIR.glob("*.json"):
                data = json.loads(config_path.read_text(encoding="utf-8"))
                if data.get("settings", {}).get("sequence_name") == old_name:
                    data["settings"]["sequence_name"] = new_name
                    self._write_json(config_path, data)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            messagebox.showerror("無法修改名稱", str(exc))
            return
        self.sequence_data[new_name] = self.sequence_data.pop(old_name)
        self.sequence_snapshots[new_name] = self.sequence_snapshots.pop(old_name, self._snapshot(self.sequence_data[new_name]))
        self.current_sequence_name.set(new_name)
        self.active_sequence_name = new_name
        if self.settings_vars["sequence_name"].get() == old_name:
            self.settings_vars["sequence_name"].set(new_name)
        self._sync_sequence_choices()
        self._mark_sequence_saved(new_name)
        self._log(f"戰鬥順序已改名：{old_name} → {new_name}")

    def _save_sequence(self):
        name = self.current_sequence_name.get()
        if not name:
            return
        try:
            name = self._safe_name(name)
            target = SEQUENCE_DIR / f"{name}.json"
            if target.exists() and not messagebox.askyesno("確認覆蓋", f"「{name}」已存在，是否覆蓋？"):
                return
            steps = self._validate_steps(self.sequence_data[name], name)
            self.sequence_data[name] = steps
            self._write_json(target, {"name": name, "steps": steps})
        except (OSError, KeyError, ValueError) as exc:
            messagebox.showerror("無法儲存", str(exc))
            return
        self._mark_sequence_saved(name)
        self._log(f"已儲存戰鬥順序：{name}")

    def _referencing_profiles(self, sequence_name: str) -> list[str]:
        references = []
        for path in CONFIG_DIR.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("settings", {}).get("sequence_name") == sequence_name:
                    references.append(path.stem)
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(references)

    def _delete_sequence(self):
        name = self.active_sequence_name
        if len(self.sequence_data) <= 1:
            messagebox.showinfo("無法刪除", "至少需保留一組戰鬥順序。")
            return
        references = self._referencing_profiles(name)
        if references:
            messagebox.showerror(
                "無法刪除",
                f"戰鬥順序「{name}」正被以下關卡設定引用：\n\n"
                + "\n".join(f"• {profile}" for profile in references)
                + "\n\n請先將這些設定改用其他戰鬥順序後再刪除。",
            )
            return
        if not messagebox.askyesno("確認刪除", f"確定要刪除戰鬥順序「{name}」嗎？\n此操作無法復原。"):
            return
        try:
            (SEQUENCE_DIR / f"{name}.json").unlink(missing_ok=True)
        except OSError as exc:
            messagebox.showerror("無法刪除", str(exc))
            return
        del self.sequence_data[name]
        self.sequence_snapshots.pop(name, None)
        self.current_sequence_name.set(sorted(self.sequence_data)[0])
        self.active_sequence_name = self.current_sequence_name.get()
        self._sync_sequence_choices()
        self._refresh_dirty_ui()
        self._log(f"已刪除戰鬥順序：{name}")

    def _refresh_steps(self):
        if not hasattr(self, "steps"):
            return
        self.steps.delete(*self.steps.get_children())
        for index, step in enumerate(self.sequence_data.get(self.current_sequence_name.get(), [])):
            wait = "3.0" if step[2] is None else str(step[2])
            self.steps.insert("", "end", iid=str(index), values=(index + 1, step[0], "" if step[1] is None else step[1], wait, step[3]))

    def _on_sequence_selected(self, _event=None):
        selected = self.current_sequence_name.get()
        if selected == self.active_sequence_name:
            return
        if not self._confirm_discard_sequence(self.active_sequence_name):
            self.current_sequence_name.set(self.active_sequence_name)
            return
        self.active_sequence_name = selected
        self._refresh_steps()
        self._refresh_dirty_ui()

    def _sync_step_editor(self):
        action = self.action_var.get()
        if action == "switch":
            self.position_combo.configure(values=(), state="disabled")
            self.position_var.set("")
            if not self.note_var.get().strip():
                self.note_var.set(SWITCH_NOTE)
            return
        values = tuple(str(number) for number in range(1, 6 if action == "skill" else 7))
        self.position_combo.configure(values=values, state="readonly")
        if self.position_var.get() not in values:
            self.position_var.set("1")

    def _step_from_editor(self) -> list[Any]:
        action = self.action_var.get()
        position = None if action == "switch" else self.position_var.get()
        raw_wait = self.wait_entry.value()
        try:
            wait = None if not raw_wait else float(raw_wait)
        except ValueError as exc:
            raise ValueError("等待秒數必須是數字，或留空使用預設 3.0 秒。") from exc
        return self._validate_steps([[action, position, wait, self.note_var.get().strip()]], "目前編輯的步驟")[0]

    def _selected_step_index(self) -> int | None:
        selected = self.steps.selection()
        return int(selected[0]) if selected else None

    def _add_step(self):
        try:
            self.sequence_data[self.current_sequence_name.get()].append(self._step_from_editor())
        except (KeyError, ValueError) as exc:
            messagebox.showerror("步驟無效", str(exc))
            return
        self._refresh_steps()
        self._refresh_dirty_ui()

    def _update_step(self):
        index = self._selected_step_index()
        if index is None:
            messagebox.showinfo("更新步驟", "請先選取一個步驟。")
            return
        try:
            self.sequence_data[self.current_sequence_name.get()][index] = self._step_from_editor()
        except (KeyError, ValueError) as exc:
            messagebox.showerror("步驟無效", str(exc))
            return
        self._refresh_steps()
        self.steps.selection_set(str(index))
        self._refresh_dirty_ui()

    def _delete_step(self):
        index = self._selected_step_index()
        if index is not None:
            del self.sequence_data[self.current_sequence_name.get()][index]
            self._refresh_steps()
            self._refresh_dirty_ui()

    def _move_step(self, direction: int):
        index = self._selected_step_index()
        if index is None:
            return
        steps = self.sequence_data[self.current_sequence_name.get()]
        target = index + direction
        if 0 <= target < len(steps):
            steps[index], steps[target] = steps[target], steps[index]
            self._refresh_steps()
            self.steps.selection_set(str(target))
            self._refresh_dirty_ui()

    def _on_step_selected(self, _event=None):
        index = self._selected_step_index()
        if index is None:
            return
        action, position, wait, note = self.sequence_data[self.current_sequence_name.get()][index]
        self.action_var.set(action)
        self.note_var.set(note)
        self._sync_step_editor()
        if position is not None:
            self.position_var.set(str(position))
        self.wait_entry.set_value(wait)

    def _update_battle_control_state(self):
        active = self.use_battle_var.get() and not self.running
        self.profile_sequence_combo.configure(state="readonly" if active else "disabled")

    def _set_editing_enabled(self, enabled: bool):
        for widget in self.editable_widgets:
            try:
                if isinstance(widget, ttk.Combobox):
                    widget.configure(state="readonly" if enabled else "disabled")
                else:
                    widget.configure(state="normal" if enabled else "disabled")
            except tk.TclError:
                pass
            if isinstance(widget, (tk.Entry, tk.Text)):
                try:
                    widget.configure(fg="#f2f2f2" if enabled else "#7f7f7f")
                except tk.TclError:
                    # ttk 元件的灰字由 disabled state 的 style 處理。
                    pass
        self._update_battle_control_state()

    def _load_backend(self):
        try:
            seer = importlib.import_module("seer")
            self.after(0, lambda: self._backend_loaded(seer))
        except Exception:
            detail = traceback.format_exc()
            self.after(0, lambda: self._backend_failed(detail))

    def _backend_loaded(self, seer):
        self.seer = seer
        self.battle_control = seer.battle_control
        self.backend_ready = True
        self.start_button.configure(state="normal")
        self._log("OCR 模型已載入，可開始執行。")

    def _backend_failed(self, detail: str):
        self._log("OCR 模型載入失敗，請檢查 Python、PaddleOCR 與 GPU 設定。")
        messagebox.showerror("載入失敗", detail)

    def _apply_settings(self, settings: dict[str, Any]):
        sequence_name = settings["sequence_name"]
        if sequence_name not in self.sequence_data:
            raise ValueError("選擇的戰鬥順序不存在。")
        sequence = tuple(tuple(step[:3]) for step in self.sequence_data[sequence_name])
        return self.seer.RunConfig(
            start_keywords=tuple(settings["start_keywords"]),
            pre_keyword=settings["pre_keyword"],
            pre2_keyword=settings["pre2_keyword"],
            post_keyword=settings["post_keyword"],
            post2_keyword=settings["post2_keyword"],
            recover_keyword=settings["recover_keyword"],
            continue_keyword=settings["continue_keyword"],
            max_rounds=settings["max_rounds"],
            use_battle_control=settings["use_battle_control"],
            battle_sequence=sequence,
            loop_skill_n=settings["loop_skill_n"],
        )

    def _start(self):
        if not self.backend_ready:
            messagebox.showinfo("尚未就緒", "OCR 模型仍在載入，請稍候。")
            return
        try:
            settings = self._config_from_form()
            run_config = self._apply_settings(settings)
        except ValueError as exc:
            messagebox.showerror("設定無效", str(exc))
            return
        self.running = True
        self.seer.shared_state.stop_event.clear()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self._set_editing_enabled(False)
        self._log("開始執行。Stop 會在目前 OCR／滑鼠操作結束後安全停止。")
        threading.Thread(target=self._run_script, args=(run_config,), daemon=True).start()

    def _run_script(self, run_config):
        writer = QueueWriter(self.log_queue)
        try:
            with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                print("等待 3 秒讓遊戲畫面回到前景後再開始掃描…")
                if self.seer.shared_state.stop_event.wait(3.0):
                    return
                self.seer.run_rounds(run_config)
        except self.seer.pyautogui.FailSafeException:
            writer.write("PyAutoGUI failsafe 已觸發：滑鼠位於左上角，已安全停止。\n")
        except Exception:
            writer.write("程式錯誤：\n" + traceback.format_exc())
        finally:
            writer.flush()
            self.after(0, self._run_finished)

    def _stop(self):
        if self.running:
            self.stop_button.configure(state="disabled")
            self.seer.shared_state.stop_event.set()
            self._log("已要求停止，等待目前操作安全結束…")

    def _run_finished(self):
        self.running = False
        self.start_button.configure(state="normal" if self.backend_ready else "disabled")
        self.stop_button.configure(state="disabled")
        self._set_editing_enabled(True)
        self._log("腳本已回到等待狀態。")

    def _log(self, message: str):
        self.log_queue.put(message)

    def _drain_log_queue(self):
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.configure(state="normal")
            self.log_text.insert(tk.END, f"{time.strftime('%H:%M:%S')}  {message}\n")
            self.log_text.see(tk.END)
            self.log_text.configure(state="disabled")
        self.after(150, self._drain_log_queue)

    def _on_close(self):
        if self._is_profile_dirty() and not self._confirm_discard_profile():
            return
        if self._is_sequence_dirty() and not self._confirm_discard_sequence(self.active_sequence_name):
            return
        if self.running and self.seer:
            self.seer.shared_state.stop_event.set()
        self.destroy()


if __name__ == "__main__":
    SeerGui().mainloop()
