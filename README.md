# Anki Global Hotkeys

在 Linux（Wayland / X11）上，切换到其他应用时仍可通过全局快捷键控制 Anki 复习。

> **仅支持 Linux**，通过 `evdev` 直接读取键盘设备实现真正的全局热键。

---

## 功能

- 任意窗口激活状态下触发 Anki 复习热键
- 支持 Wayland 和 X11
- 拦截模式：热键组合不传递给其他程序
- 配置界面：Tools → 全局热键配置
- 热键修改后实时生效，无需重启
- 自动检测 snap / flatpak 沙箱并给出提示

## 默认热键

| 快捷键 | 功能 |
|--------|------|
| `Ctrl+Shift+Space` | 显示答案 |
| `Ctrl+Shift+1` | Again |
| `Ctrl+Shift+2` | Hard |
| `Ctrl+Shift+3` | Good |
| `Ctrl+Shift+4` | Easy |

所有热键均可在配置界面自由修改。

## 安装要求

- Linux 系统（Wayland 或 X11）
- Anki **原生安装包**（不支持 snap / flatpak）
- Python 3.10+
- 以下 Python 库：

```bash
pip install evdev requests
```

- 用户需加入 `input` 用户组（插件首次运行时会自动提示）：

```bash
sudo usermod -a -G input $USER
# 然后注销并重新登录
```

## 安装插件

### 方式一：从 AnkiWeb 安装（推荐）

在 Anki 中：工具 → 插件 → 获取插件，输入插件代码。

### 方式二：手动安装

1. 从 [Releases](../../releases/latest) 下载 `anki-global-hotkeys.ankiaddon`
2. Anki → 工具 → 插件 → 从文件安装插件
3. 选择下载的文件，重启 Anki

## 使用方法

1. 安装插件并重启 Anki
2. 若首次运行缺少权限，按照弹出提示执行授权命令并重新登录
3. Anki 启动后 daemon 自动在后台运行
4. 切换到任意其他应用，按热键即可控制复习

## 配置

Tools → 全局热键配置，可修改：

- 每个动作的快捷键
- 拦截模式开关（防止热键同时触发其他程序的快捷键）

## 工作原理

插件内置一个 Python daemon，通过 `evdev` 独立于显示服务器直接监听键盘事件，
触发热键时调用 [AnkiConnect](https://ankiweb.net/shared/info/2055492159) HTTP API 控制 Anki。

## 依赖

- [AnkiConnect](https://ankiweb.net/shared/info/2055492159)（需单独安装）
- [evdev](https://python-evdev.readthedocs.io/)
- [requests](https://requests.readthedocs.io/)
