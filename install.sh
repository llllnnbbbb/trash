#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
UUID="dock-trash@ubuntu-trash"
SRC="$ROOT/extension"
OUT="$ROOT/dist"

mkdir -p "$OUT"
find "$SRC/helper" -type d -name '__pycache__' -prune -exec rm -rf {} +

pack_args=(
    --force
    --out-dir="$OUT"
    --extra-source=confirmDialog.js
    --extra-source=helperClient.js
    --extra-source=trashMonitor.js
    --extra-source=helper
)

( cd "$SRC" && gnome-extensions pack "${pack_args[@]}" )

ZIP="$OUT/${UUID}.shell-extension.zip"
if [[ ! -f "$ZIP" ]]; then
    echo "打包失败，未生成 $ZIP" >&2
    exit 1
fi

gnome-extensions install --force "$ZIP"

python3 - "$UUID" <<'PY'
import subprocess
import sys

uuid = sys.argv[1]
raw = subprocess.check_output(
    ["gsettings", "get", "org.gnome.shell", "enabled-extensions"],
    text=True,
).strip()
# GSettings prints a GLib.Variant list, e.g. ['a', 'b'] or @as []
from ast import literal_eval

try:
    enabled = list(literal_eval(raw.removeprefix("@as ")))
except Exception:
    enabled = []
if uuid not in enabled:
    enabled.append(uuid)
    serialized = "[" + ", ".join("'" + item.replace("'", r"\'") + "'" for item in enabled) + "]"
    subprocess.check_call(["gsettings", "set", "org.gnome.shell", "enabled-extensions", serialized])
    print(f"已写入 enabled-extensions：{uuid}")
else:
    print(f"enabled-extensions 已包含 {uuid}")
PY

if gnome-extensions enable "$UUID" 2>/dev/null; then
    echo "已启用 $UUID"
else
    cat <<EOF

扩展已安装到：
  $HOME/.local/share/gnome-shell/extensions/${UUID}

当前 GNOME Shell 从开机起就在运行，还不知道这个新扩展，所以现在执行
  gnome-extensions enable ${UUID}
会提示「扩展不存在」。这是 GNOME 50 + Wayland 的正常限制。

请注销并重新登录。登录后扩展会按 enabled-extensions 自动启用。
可用下面命令确认：
  gnome-extensions info ${UUID}
EOF
fi
