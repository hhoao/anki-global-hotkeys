"""
Global Hotkeys — Anki 插件
Anki 启动时自动拉起 daemon，关闭时自动停止。
配置界面：Tools → 全局热键配置…
"""
import atexit
import glob
import json
import os
import signal
import subprocess
from pathlib import Path

from aqt import gui_hooks, mw
from aqt.qt import (
    QAction, QApplication, QCheckBox, QDialog, QDialogButtonBox, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTimer, QVBoxLayout, Qt,
)
from aqt.utils import tooltip

CONFIG_FILE   = Path.home() / ".config" / "anki_global_hotkeys.json"
PID_FILE      = Path.home() / ".cache"  / "anki_global_hotkeys.pid"
DAEMON_SCRIPT = Path(__file__).parent / "daemon.py"

DEFAULT_CONFIG = {
    "suppress": False,
    "hotkeys": {
        "show_answer": "ctrl+shift+space",
        "again":       "ctrl+shift+1",
        "hard":        "ctrl+shift+2",
        "good":        "ctrl+shift+3",
        "easy":        "ctrl+shift+4",
    },
}

ACTION_LABELS = {
    "show_answer": "显示答案",
    "again":       "Again（重来）",
    "hard":        "Hard（困难）",
    "good":        "Good（良好）",
    "easy":        "Easy（简单）",
}


# ── 沙箱 & 权限检测 ───────────────────────────────────────────────
def detect_sandbox() -> str:
    """返回 'snap'、'flatpak' 或 ''（原生）。"""
    if os.environ.get("SNAP") or str(Path(__file__)).startswith("/snap/"):
        return "snap"
    if os.environ.get("FLATPAK_ID") or Path("/.flatpak-info").exists():
        return "flatpak"
    return ""


def check_input_permission() -> bool:
    """检查当前用户是否能读取 /dev/input/event* 设备。"""
    devices = glob.glob("/dev/input/event*")
    if not devices:
        return False
    return os.access(devices[0], os.R_OK)


# ── 配置 ──────────────────────────────────────────────────────────
def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return json.loads(json.dumps(DEFAULT_CONFIG))


def save_config(config: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── Daemon ────────────────────────────────────────────────────────
def is_daemon_running() -> bool:
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, ValueError, OSError):
        return False


def start_daemon() -> None:
    if is_daemon_running():
        return
    try:
        subprocess.Popen(
            ["python3", str(DAEMON_SCRIPT), "--config", str(CONFIG_FILE)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        tooltip(f"启动 daemon 失败：{e}")


def stop_daemon() -> None:
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, ValueError, OSError):
            pass
        finally:
            PID_FILE.unlink(missing_ok=True)


def restart_daemon() -> None:
    stop_daemon()
    QTimer.singleShot(800, start_daemon)


# ── 权限设置引导对话框 ─────────────────────────────────────────────
class PermissionSetupDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("全局热键 — 需要一次性授权")
        self.setMinimumWidth(520)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)

        # 说明
        info = QLabel(
            "全局热键功能需要读取键盘设备（<code>/dev/input/</code>），"
            "但您的账户当前没有访问权限。\n\n"
            "只需在终端运行一条命令，<b>重新登录后永久生效</b>，之后无需任何操作。"
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(info)

        # 命令框
        cmd_group = QGroupBox("在终端中运行（只需一次）")
        cmd_layout = QVBoxLayout(cmd_group)

        cmd_text = f"sudo usermod -a -G input {os.environ.get('USER', '$USER')}"
        cmd_label = QLabel(f"<pre style='margin:0'>{cmd_text}</pre>")
        cmd_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        cmd_label.setStyleSheet(
            "background:#1e1e1e; color:#d4d4d4; padding:10px; border-radius:4px;"
        )
        cmd_layout.addWidget(cmd_label)

        copy_btn = QPushButton("复制命令")
        copy_btn.setFixedWidth(100)
        copy_btn.clicked.connect(lambda: (
            QApplication.clipboard().setText(cmd_text),
            tooltip("已复制"),
        ))
        cmd_layout.addWidget(copy_btn, alignment=Qt.AlignmentFlag.AlignRight)
        root.addWidget(cmd_group)

        # 步骤说明
        steps = QLabel(
            "操作步骤：\n"
            "  1. 点击「复制命令」\n"
            "  2. 打开终端，粘贴并回车执行\n"
            "  3. 输入您的系统密码\n"
            "  4. 注销并重新登录（或重启）\n"
            "  5. 重新打开 Anki，热键将自动启动"
        )
        steps.setStyleSheet("color: #555; font-size: 12px;")
        root.addWidget(steps)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("知道了")
        btns.accepted.connect(self.accept)
        root.addWidget(btns)


# ── 沙箱警告对话框 ───────────────────────────────────────────────
_SANDBOX_INFO = {
    "snap": {
        "title": "不支持 snap 版 Anki",
        "desc": (
            "检测到 Anki 以 <b>snap</b> 沙箱模式运行。<br><br>"
            "snap 的严格沙箱会阻止插件访问键盘设备（<code>/dev/input/</code>），"
            "全局热键功能<b>无法在 snap 版本中正常工作</b>。"
        ),
        "solution": (
            "<b>推荐方案：改用官方安装包</b><br>"
            "1. 卸载 snap 版本：<code>sudo snap remove anki-desktop</code><br>"
            "2. 从 <b>ankiweb.net</b> 下载 Linux 官方安装包（<code>.tar.zst</code>）<br>"
            "3. 解压后运行 <code>sudo ./install.sh</code><br>"
            "4. 原有卡片数据迁移：将 <code>~/snap/anki-desktop/common/</code> "
            "中的内容复制到 <code>~/.local/share/Anki2/</code>"
        ),
    },
    "flatpak": {
        "title": "Flatpak 版 Anki 需要额外授权",
        "desc": (
            "检测到 Anki 以 <b>flatpak</b> 沙箱模式运行。<br><br>"
            "flatpak 默认不允许访问键盘设备，需要手动授予权限，"
            "或改用官方安装包。"
        ),
        "solution": (
            "<b>方案 A：授予设备权限（在终端运行）</b><br>"
            "<code>flatpak override --user --device=all {flatpak_id}</code><br>"
            "然后重启 Anki。<br><br>"
            "<b>方案 B（推荐）：改用官方安装包</b><br>"
            "从 <b>ankiweb.net</b> 下载 Linux 官方安装包，彻底避免沙箱限制。"
        ),
    },
}


class SandboxWarningDialog(QDialog):
    def __init__(self, sandbox: str, parent=None):
        super().__init__(parent)
        info = _SANDBOX_INFO[sandbox]
        flatpak_id = os.environ.get("FLATPAK_ID", "net.ankiweb.Anki")
        self.setWindowTitle(info["title"])
        self.setMinimumWidth(540)
        root = QVBoxLayout(self)

        desc = QLabel(info["desc"])
        desc.setWordWrap(True)
        desc.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(desc)

        sol_group = QGroupBox("解决方法")
        sol_layout = QVBoxLayout(sol_group)
        sol = QLabel(info["solution"].format(flatpak_id=flatpak_id))
        sol.setWordWrap(True)
        sol.setTextFormat(Qt.TextFormat.RichText)
        sol.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        sol_layout.addWidget(sol)
        root.addWidget(sol_group)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("知道了")
        btns.accepted.connect(self.accept)
        root.addWidget(btns)


# ── 配置对话框 ────────────────────────────────────────────────────
class HotkeyConfigDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("全局热键配置")
        self.setMinimumWidth(460)
        self._config = load_config()
        self._inputs: dict[str, QLineEdit] = {}
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)

        # ── 沙箱 / 权限警告 ────────────────────────────────────────
        sandbox = detect_sandbox()
        if sandbox:
            info = _SANDBOX_INFO[sandbox]
            warn = QLabel(
                f"⚠ 检测到 {sandbox} 沙箱，全局热键功能受限。"
                f"<a href='#sandbox'>查看解决方法</a>"
            )
            warn.setStyleSheet(
                "background:#f8d7da; color:#721c24; padding:8px; border-radius:4px;"
            )
            warn.setTextFormat(Qt.TextFormat.RichText)
            warn.linkActivated.connect(
                lambda _: SandboxWarningDialog(sandbox, self).exec()
            )
            root.addWidget(warn)
        elif not check_input_permission():
            warn = QLabel(
                "⚠ 缺少键盘设备读取权限，热键无法工作。"
                "<a href='#setup'>查看设置方法</a>"
            )
            warn.setStyleSheet(
                "background:#fff3cd; color:#856404; padding:8px; border-radius:4px;"
            )
            warn.setTextFormat(Qt.TextFormat.RichText)
            warn.linkActivated.connect(lambda _: PermissionSetupDialog(self).exec())
            root.addWidget(warn)

        # ── daemon 状态 ────────────────────────────────────────────
        status_group = QGroupBox("Daemon 状态")
        status_layout = QHBoxLayout(status_group)
        self._status_label = QLabel()
        self._toggle_btn = QPushButton()
        self._toggle_btn.setFixedWidth(80)
        self._toggle_btn.clicked.connect(self._toggle_daemon)
        status_layout.addWidget(self._status_label)
        status_layout.addStretch()
        status_layout.addWidget(self._toggle_btn)
        root.addWidget(status_group)
        self._refresh_status()

        # ── 拦截模式 ───────────────────────────────────────────────
        self._suppress_check = QCheckBox(
            "拦截模式：吞掉热键组合，防止触发其他程序的快捷键（变更后自动重启 daemon）"
        )
        self._suppress_check.setChecked(self._config.get("suppress", False))
        root.addWidget(self._suppress_check)

        # ── 热键表格 ───────────────────────────────────────────────
        hotkey_group = QGroupBox("热键设置")
        grid = QGridLayout(hotkey_group)
        grid.setColumnStretch(1, 1)
        for col, text in enumerate(["功能", "快捷键"]):
            lbl = QLabel(text)
            lbl.setStyleSheet("font-weight: bold;")
            grid.addWidget(lbl, 0, col)
        hotkeys = self._config.get("hotkeys", DEFAULT_CONFIG["hotkeys"])
        for row, (action, label) in enumerate(ACTION_LABELS.items(), start=1):
            grid.addWidget(QLabel(label), row, 0)
            edit = QLineEdit(hotkeys.get(action, ""))
            edit.setPlaceholderText("例：ctrl+shift+1")
            self._inputs[action] = edit
            grid.addWidget(edit, row, 1)
        root.addWidget(hotkey_group)

        hint = QLabel("修饰键：ctrl  shift  alt　　普通键：space  enter  1~9  a~z  f1~f12")
        hint.setStyleSheet("color: gray; font-size: 11px;")
        root.addWidget(hint)

        btn_reset = QPushButton("恢复默认")
        btn_reset.clicked.connect(self._reset)
        std_btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        std_btns.accepted.connect(self._save)
        std_btns.rejected.connect(self.reject)
        btn_row = QHBoxLayout()
        btn_row.addWidget(btn_reset)
        btn_row.addStretch()
        btn_row.addWidget(std_btns)
        root.addLayout(btn_row)

    def _refresh_status(self):
        if not check_input_permission():
            self._status_label.setText("⚠ 权限不足")
            self._status_label.setStyleSheet("color: #856404;")
            self._toggle_btn.setEnabled(False)
        elif is_daemon_running():
            self._status_label.setText("● 运行中")
            self._status_label.setStyleSheet("color: green; font-weight: bold;")
            self._toggle_btn.setText("停止")
            self._toggle_btn.setEnabled(True)
        else:
            self._status_label.setText("○ 未运行")
            self._status_label.setStyleSheet("color: gray;")
            self._toggle_btn.setText("启动")
            self._toggle_btn.setEnabled(True)

    def _toggle_daemon(self):
        if is_daemon_running():
            stop_daemon()
        else:
            start_daemon()
        self._refresh_status()

    def _reset(self):
        for action, edit in self._inputs.items():
            edit.setText(DEFAULT_CONFIG["hotkeys"].get(action, ""))
        self._suppress_check.setChecked(False)

    def _save(self):
        old_suppress = self._config.get("suppress", False)
        new_suppress = self._suppress_check.isChecked()
        save_config({
            "suppress": new_suppress,
            "hotkeys": {
                action: edit.text().strip()
                for action, edit in self._inputs.items()
            },
        })
        if old_suppress != new_suppress:
            tooltip("拦截模式已变更，daemon 正在重启…")
            restart_daemon()
        else:
            tooltip("配置已保存，daemon 将在 2 秒内自动更新热键")
        self.accept()


# ── Anki 生命周期 ─────────────────────────────────────────────────
def on_main_window_init():
    action = QAction("全局热键配置…", mw)
    action.triggered.connect(lambda: HotkeyConfigDialog(mw).exec())
    mw.form.menuTools.addSeparator()
    mw.form.menuTools.addAction(action)

    sandbox = detect_sandbox()
    flag = CONFIG_FILE.parent / ".anki_hotkey_setup_shown"

    if sandbox:
        # snap / flatpak：只弹一次沙箱警告，不尝试启动 daemon
        if not flag.exists():
            flag.touch()
            QTimer.singleShot(1500, lambda: SandboxWarningDialog(sandbox, mw).exec())
    elif check_input_permission():
        start_daemon()
        atexit.register(stop_daemon)
    else:
        # 原生安装但缺少 input 组权限：弹一次授权引导
        if not flag.exists():
            flag.touch()
            QTimer.singleShot(1500, lambda: PermissionSetupDialog(mw).exec())


if not CONFIG_FILE.exists():
    save_config(DEFAULT_CONFIG)

gui_hooks.main_window_did_init.append(on_main_window_init)
