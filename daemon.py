#!/usr/bin/env python3
"""
Anki Global Hotkeys Daemon
读取 ~/.config/anki_global_hotkeys.json，通过 evdev 监听全局热键，
调用 AnkiConnect 控制复习。支持运行时热重载配置。

依赖：pip install evdev requests
权限：sudo usermod -a -G input $USER  （然后重新登录）
"""
import argparse
import asyncio
import atexit
import json
import os
import sys
from pathlib import Path

import requests
from evdev import InputDevice, UInput, ecodes, list_devices

# ── 命令行参数 ────────────────────────────────────────────────────
_parser = argparse.ArgumentParser(add_help=False)
_parser.add_argument("--config", default=None)
_args, _ = _parser.parse_known_args()

ANKI_URL    = "http://localhost:8765"
CONFIG_FILE = Path(_args.config) if _args.config else Path.home() / ".config" / "anki_global_hotkeys.json"
PID_FILE    = Path.home() / ".cache" / "anki_global_hotkeys.pid"

DEFAULT_HOTKEYS = {
    "show_answer": "ctrl+shift+space",
    "again":       "ctrl+shift+1",
    "hard":        "ctrl+shift+2",
    "good":        "ctrl+shift+3",
    "easy":        "ctrl+shift+4",
}

# ── 键名映射 ──────────────────────────────────────────────────────
_KEY_NAME_MAP: dict[str, int] = {
    "space":  ecodes.KEY_SPACE,
    "enter":  ecodes.KEY_ENTER,
    "tab":    ecodes.KEY_TAB,
    "esc":    ecodes.KEY_ESC,
    "escape": ecodes.KEY_ESC,
    **{str(i): getattr(ecodes, f"KEY_{i}") for i in range(10)},
    **{c: getattr(ecodes, f"KEY_{c.upper()}") for c in "abcdefghijklmnopqrstuvwxyz"},
    **{f"f{i}": getattr(ecodes, f"KEY_F{i}") for i in range(1, 13)},
}

_MODIFIER_GROUPS: dict[str, frozenset[int]] = {
    "ctrl":  frozenset({ecodes.KEY_LEFTCTRL,  ecodes.KEY_RIGHTCTRL}),
    "shift": frozenset({ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT}),
    "alt":   frozenset({ecodes.KEY_LEFTALT,   ecodes.KEY_RIGHTALT}),
}

ALL_MODIFIERS: frozenset[int] = frozenset(
    k for s in _MODIFIER_GROUPS.values() for k in s
)


# ── 热键解析 ──────────────────────────────────────────────────────
def parse_hotkey(hotkey_str: str) -> tuple[frozenset[str], int | None]:
    mods: set[str] = set()
    trigger: int | None = None
    for part in hotkey_str.lower().split("+"):
        part = part.strip()
        if part in _MODIFIER_GROUPS:
            mods.add(part)
        elif part in ("lctrl", "rctrl"):
            mods.add("ctrl")
        elif part in ("lshift", "rshift"):
            mods.add("shift")
        elif part in ("lalt", "ralt"):
            mods.add("alt")
        else:
            trigger = _KEY_NAME_MAP.get(part)
            if trigger is None:
                print(f"[警告] 未知键名: '{part}'，跳过此热键", flush=True)
    return frozenset(mods), trigger


# ── AnkiConnect ───────────────────────────────────────────────────
def anki_call(action: str, **params):
    payload = {"action": action, "version": 6, "params": params}
    try:
        resp = requests.post(ANKI_URL, json=payload, timeout=2)
        result = resp.json()
        if result.get("error"):
            print(f"[Anki] 错误: {result['error']}", flush=True)
        return result
    except requests.exceptions.ConnectionError:
        print("[Anki] 无法连接，请确认 Anki 已运行", flush=True)
    except requests.exceptions.Timeout:
        print("[Anki] 请求超时", flush=True)
    return None


# ── 动作 ──────────────────────────────────────────────────────────
def _show_answer():
    print("[热键] 显示答案", flush=True)
    anki_call("guiShowAnswer")


def _answer(ease: int):
    labels = {1: "Again", 2: "Hard", 3: "Good", 4: "Easy"}
    print(f"[热键] 评分 {ease} - {labels[ease]}", flush=True)
    anki_call("guiAnswerCard", ease=ease)


ACTIONS = {
    "show_answer": _show_answer,
    "again":  lambda: _answer(1),
    "hard":   lambda: _answer(2),
    "good":   lambda: _answer(3),
    "easy":   lambda: _answer(4),
}


# ── 配置 ──────────────────────────────────────────────────────────
class Config:
    def __init__(self):
        self._mtime: float = 0.0
        self.hotkeys: dict[str, tuple[frozenset[str], int | None]] = {}
        self.suppress: bool = False
        self.reload()

    def reload(self) -> bool:
        try:
            mtime = CONFIG_FILE.stat().st_mtime
        except FileNotFoundError:
            mtime = 0.0
        if mtime == self._mtime:
            return False
        self._mtime = mtime
        raw: dict = DEFAULT_HOTKEYS.copy()
        data: dict = {}
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                raw.update(data.get("hotkeys", {}))
            except Exception as e:
                print(f"[配置] 读取失败: {e}，使用默认值", flush=True)
        self.suppress = bool(data.get("suppress", False))
        self.hotkeys = {
            action: parse_hotkey(hk_str)
            for action, hk_str in raw.items()
            if action in ACTIONS
        }
        return True


# ── 设备 ──────────────────────────────────────────────────────────
def find_keyboards() -> list[InputDevice]:
    result = []
    for path in list_devices():
        try:
            dev = InputDevice(path)
            caps = dev.capabilities()
            keys = caps.get(ecodes.EV_KEY, [])
            if ecodes.KEY_A in keys and ecodes.KEY_SPACE in keys:
                result.append(dev)
        except (PermissionError, OSError):
            pass
    return result


def create_passthrough(keyboards: list[InputDevice]) -> UInput | None:
    """独占键盘后，创建 UInput 虚拟设备用于透传非热键事件。"""
    try:
        ui = UInput.from_device(*keyboards, name="anki-hotkeys-passthrough")
        print("[透传] UInput 虚拟设备已创建", flush=True)
        return ui
    except PermissionError:
        print("[错误] 无法创建 UInput 设备（权限不足）", flush=True)
        return None
    except Exception as e:
        print(f"[错误] 无法创建 UInput 设备: {e}", flush=True)
        return None


# ── 热键引擎 ──────────────────────────────────────────────────────
class HotkeyEngine:
    def __init__(self, config: Config):
        self.config = config
        self._held: set[int] = set()

    def on_key_event(self, key_code: int, value: int) -> bool:
        """处理按键事件。返回 True 表示该事件已被热键消费，不应透传。"""
        if value == 1:   # 按下
            self._held.add(key_code)
            if key_code not in ALL_MODIFIERS:
                return self._check(key_code)
        elif value == 0: # 释放
            self._held.discard(key_code)
        return False

    def _check(self, trigger: int) -> bool:
        for action, (req_mods, req_trigger) in self.config.hotkeys.items():
            if req_trigger != trigger:
                continue
            if all(self._held & _MODIFIER_GROUPS[m] for m in req_mods):
                ACTIONS[action]()
                return True
        return False


# ── 异步循环 ──────────────────────────────────────────────────────
async def read_device(dev: InputDevice, engine: HotkeyEngine,
                      passthrough: UInput | None = None):
    try:
        async for event in dev.async_read_loop():
            if event.type == ecodes.EV_KEY:
                consumed = engine.on_key_event(event.code, event.value)
                if passthrough and not consumed:
                    passthrough.write(event.type, event.code, event.value)
            elif passthrough:
                if event.type == ecodes.EV_SYN and event.code == ecodes.SYN_REPORT:
                    passthrough.syn()
                else:
                    passthrough.write(event.type, event.code, event.value)
    except OSError:
        pass


async def config_watcher(config: Config, interval: float = 2.0):
    while True:
        await asyncio.sleep(interval)
        prev_suppress = config.suppress
        if config.reload():
            if config.suppress != prev_suppress:
                # suppress 变更需要重新 grab/ungrab 设备，直接退出让 add-on 重启
                print("[配置] suppress 设置变更，daemon 退出以重启应用变更", flush=True)
                sys.exit(0)
            print("[配置] 检测到变更，已热重载", flush=True)


async def main():
    print("Anki Global Hotkeys Daemon 启动中...", flush=True)

    keyboards = find_keyboards()
    if not keyboards:
        print("[错误] 未找到可读的键盘设备", flush=True)
        print("  请确认已加入 input 组：sudo usermod -a -G input $USER", flush=True)
        sys.exit(1)

    config = Config()

    print(f"[设备] 监听 {len(keyboards)} 个键盘设备", flush=True)
    for kb in keyboards:
        print(f"  · {kb.path}  {kb.name}", flush=True)

    # 打印热键配置
    raw_hotkeys = DEFAULT_HOTKEYS.copy()
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            raw_hotkeys.update(data.get("hotkeys", {}))
        except Exception:
            pass
    print(f"\n[配置] 来源: {CONFIG_FILE}", flush=True)
    for action in ACTIONS:
        print(f"  {action:12s}: {raw_hotkeys.get(action, '未设置')}", flush=True)

    # suppress 模式：独占键盘 + 创建透传设备
    passthrough: UInput | None = None
    if config.suppress:
        print("\n[模式] 拦截模式：热键组合不会传递给其他程序", flush=True)
        grabbed = []
        for kb in keyboards:
            try:
                kb.grab()
                grabbed.append(kb)
            except Exception as e:
                print(f"[警告] 无法独占 {kb.name}: {e}", flush=True)
        if grabbed:
            passthrough = create_passthrough(grabbed)
            if passthrough is None:
                # 创建透传失败，释放独占以免键盘卡死
                for kb in grabbed:
                    try: kb.ungrab()
                    except: pass
                print("[警告] 回退到普通模式", flush=True)
    else:
        print("\n[模式] 普通模式：热键触发时其他程序仍会收到按键", flush=True)

    print("\n全局热键已激活，按 Ctrl+C 退出\n", flush=True)

    engine = HotkeyEngine(config)
    tasks = [
        asyncio.create_task(read_device(dev, engine, passthrough))
        for dev in keyboards
    ]
    tasks.append(asyncio.create_task(config_watcher(config)))

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    finally:
        if passthrough:
            passthrough.close()
        if config.suppress:
            for kb in keyboards:
                try: kb.ungrab()
                except: pass


# ── 入口 ──────────────────────────────────────────────────────────
def _write_pid():
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))

def _remove_pid():
    PID_FILE.unlink(missing_ok=True)

if __name__ == "__main__":
    _write_pid()
    atexit.register(_remove_pid)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n已退出", flush=True)
