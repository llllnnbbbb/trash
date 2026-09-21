#!/usr/bin/env python3
"""Classify dropped apps/files and uninstall or trash them.

Used by the GNOME Shell extension dock-trash@ubuntu-trash.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# GI 弃用警告会污染 stdout，扩展会把它当成 JSON 解析失败。
warnings.filterwarnings("ignore", category=DeprecationWarning)
try:
    from gi import PyGIDeprecationWarning

    warnings.filterwarnings("ignore", category=PyGIDeprecationWarning)
except Exception:
    pass


def log(message: str) -> None:
    line = f"dock-trash: {message}"
    try:
        import syslog

        syslog.syslog(syslog.LOG_INFO, line)
    except Exception:
        pass
    try:
        from datetime import datetime

        log_path = Path.home() / ".cache" / "dock-trash.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now().isoformat(timespec='seconds')} {line}\n")
    except OSError:
        pass

APPIMAGE_MAGIC = b"AI\x02"
PACKAGE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]+$")
APP_ID_RE = re.compile(r"^[A-Za-z0-9@._+-]+\.desktop$")
FIELD_CODE_RE = re.compile(r"%[fFuUdDnNickvm]")

PROTECTED_PACKAGES = {
    "apt",
    "apt-utils",
    "base-files",
    "base-passwd",
    "bash",
    "coreutils",
    "dash",
    "dbus",
    "dpkg",
    "gdm3",
    "gnome-control-center",
    "gnome-session",
    "gnome-shell",
    "gnome-shell-common",
    "gnome-shell-extension-ubuntu-dock",
    "init",
    "libc6",
    "libglib2.0-0",
    "libglib2.0-0t64",
    "login",
    "mutter",
    "network-manager",
    "passwd",
    "policykit-1",
    "sudo",
    "systemd",
    "systemd-sysv",
    "ubuntu-desktop",
    "ubuntu-desktop-minimal",
    "ubuntu-minimal",
    "ubuntu-session",
    "ubuntu-standard",
}

PROTECTED_PREFIXES = (
    "linux-image",
    "linux-headers",
    "linux-modules",
    "linux-generic",
    "ubuntu-desktop",
    "gnome-shell",
)

PROTECTED_FILE_PREFIXES = (
    "/bin/",
    "/boot/",
    "/dev/",
    "/etc/",
    "/lib/",
    "/lib64/",
    "/proc/",
    "/run/",
    "/sbin/",
    "/sys/",
    "/usr/",
    "/var/lib/",
    "/var/log/",
)

KIND_FILE = "file"
KIND_DEB = "deb"
KIND_APPIMAGE = "appimage"
KIND_SNAP = "snap"
KIND_FLATPAK = "flatpak"
KIND_SYSTEM = "system"
KIND_UNKNOWN = "unknown"
KIND_ERROR = "error"


@dataclass
class DesktopMeta:
    app_id: str | None = None
    name: str | None = None
    filename: str | None = None
    exec_line: str | None = None
    executable: str | None = None
    try_exec: str | None = None
    extra: dict[str, str] = field(default_factory=dict)


@dataclass
class InspectResult:
    ok: bool
    kind: str
    blocked: bool = False
    reason: str | None = None
    name: str | None = None
    app_id: str | None = None
    package: str | None = None
    desktop_file: str | None = None
    exec_line: str | None = None
    paths: list[str] = field(default_factory=list)
    rdepends: list[str] = field(default_factory=list)
    action: str | None = None
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "kind": self.kind,
            "blocked": self.blocked,
            "reason": self.reason,
            "name": self.name,
            "app_id": self.app_id,
            "package": self.package,
            "desktop_file": self.desktop_file,
            "exec_line": self.exec_line,
            "paths": self.paths,
            "rdepends": self.rdepends,
            "action": self.action,
            "summary": self.summary,
        }


def which(name: str) -> str | None:
    return shutil.which(name)


def run_cmd(argv: list[str], timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def strip_field_codes(exec_line: str) -> str:
    return FIELD_CODE_RE.sub("", exec_line).strip()


def split_exec(exec_line: str) -> list[str]:
    try:
        import shlex

        return shlex.split(strip_field_codes(exec_line), posix=True)
    except ValueError:
        return strip_field_codes(exec_line).split()


def first_real_binary(tokens: list[str]) -> str | None:
    skip = {"env", "nice", "nohup", "setsid"}
    i = 0
    while i < len(tokens):
        token = tokens[i]
        base = os.path.basename(token)
        if base in skip:
            i += 1
            while i < len(tokens) and "=" in tokens[i] and not tokens[i].startswith("-"):
                i += 1
            continue
        if token.startswith("-"):
            i += 1
            continue
        return token
    return tokens[0] if tokens else None


def expand_path(value: str | None) -> str | None:
    if not value:
        return None
    return os.path.abspath(os.path.expanduser(value))


def parse_desktop_file(path: str) -> DesktopMeta:
    data: dict[str, str] = {}
    name = None
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        in_entry = False
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("["):
                in_entry = line == "[Desktop Entry]"
                continue
            if not in_entry or "=" not in line:
                continue
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip()
            if key.strip() == "Name" and name is None:
                name = value.strip()

    filename = os.path.abspath(path)
    app_id = os.path.basename(filename)
    executable = expand_path(data.get("TryExec")) if data.get("TryExec") and os.path.isabs(
        data.get("TryExec", "")
    ) else data.get("TryExec")
    return DesktopMeta(
        app_id=app_id,
        name=name or app_id,
        filename=filename,
        exec_line=data.get("Exec"),
        executable=executable,
        try_exec=data.get("TryExec"),
        extra=data,
    )


def load_desktop_from_app_id(app_id: str) -> DesktopMeta:
    if not APP_ID_RE.match(app_id):
        raise ValueError(f"invalid app id: {app_id}")

    gi_meta = _load_desktop_via_gi(app_id)
    if gi_meta:
        return gi_meta

    search_dirs = [
        Path.home() / ".local/share/applications",
        Path("/usr/share/applications"),
        Path("/usr/local/share/applications"),
        Path("/var/lib/snapd/desktop/applications"),
        Path("/var/lib/flatpak/exports/share/applications"),
        Path.home() / ".local/share/flatpak/exports/share/applications",
    ]
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        search_dirs.insert(0, Path(xdg) / "applications")

    for directory in search_dirs:
        candidate = directory / app_id
        if candidate.is_file():
            return parse_desktop_file(str(candidate))
    raise FileNotFoundError(f"desktop file not found for {app_id}")


def _load_desktop_via_gi(app_id: str) -> DesktopMeta | None:
    try:
        import gi

        gi.require_version("GioUnix", "2.0")
        from gi.repository import GioUnix
    except (ImportError, ValueError):
        return None

    info = GioUnix.DesktopAppInfo.new(app_id)
    if info is None:
        return None

    extra: dict[str, str] = {}
    for key in (
        "X-AppImage-Version",
        "X-AppImage-Arch",
        "X-AppImage-Name",
        "X-AppImageLauncher-Dest",
        "X-SnapInstanceName",
        "X-Flatpak",
        "X-SnapAppName",
    ):
        value = info.get_string(key)
        if value:
            extra[key] = value

    filename = info.get_filename()
    return DesktopMeta(
        app_id=info.get_id() or app_id,
        name=info.get_display_name() or info.get_name() or app_id,
        filename=filename,
        exec_line=info.get_commandline(),
        executable=info.get_executable(),
        try_exec=info.get_string("TryExec"),
        extra=extra,
    )


def looks_like_appimage_path(path: str | None) -> bool:
    if not path:
        return False
    return path.lower().endswith(".appimage")


def has_appimage_magic(path: str) -> bool:
    try:
        with open(path, "rb") as handle:
            handle.seek(8)
            return handle.read(3) == APPIMAGE_MAGIC
    except OSError:
        return False


def is_appimage(path: str | None) -> bool:
    if not path or not os.path.isfile(path):
        return False
    return looks_like_appimage_path(path) or has_appimage_magic(path)


def desktop_is_appimage(meta: DesktopMeta) -> bool:
    for key, value in meta.extra.items():
        if key.startswith("X-AppImage"):
            return True
        if "appimage" in key.lower():
            return True
        if "appimage" in value.lower():
            return True

    tokens = split_exec(meta.exec_line or "")
    binary = first_real_binary(tokens)
    candidates = [
        expand_path(binary) if binary else None,
        expand_path(meta.executable),
        expand_path(meta.try_exec),
    ]
    exec_joined = meta.exec_line or ""
    if ".appimage" in exec_joined.lower():
        return True
    return any(is_appimage(item) or looks_like_appimage_path(item) for item in candidates if item)


def desktop_is_snap(meta: DesktopMeta) -> bool:
    if meta.extra.get("X-SnapInstanceName") or meta.extra.get("X-SnapAppName"):
        return True
    filename = meta.filename or ""
    if "/snapd/desktop/applications/" in filename or filename.startswith("/snap/"):
        return True
    exec_line = (meta.exec_line or "").lower()
    if "snap run" in exec_line or "/snap/" in exec_line:
        return True
    executable = meta.executable or ""
    return executable.startswith("/snap/") or os.path.basename(executable) == "snap"


def desktop_is_flatpak(meta: DesktopMeta) -> bool:
    if meta.extra.get("X-Flatpak"):
        return True
    filename = meta.filename or ""
    if "/flatpak/" in filename:
        return True
    exec_line = (meta.exec_line or "").lower()
    executable = meta.executable or ""
    return "flatpak" in exec_line or os.path.basename(executable) == "flatpak"


def resolve_binary(meta: DesktopMeta) -> str | None:
    tokens = split_exec(meta.exec_line or "")
    binary = first_real_binary(tokens)
    if binary and os.path.isabs(binary) and os.path.exists(binary):
        return binary
    if meta.executable:
        if os.path.isabs(meta.executable) and os.path.exists(meta.executable):
            return meta.executable
        found = which(meta.executable)
        if found:
            return found
    if binary:
        found = which(binary)
        if found:
            return found
        expanded = expand_path(binary)
        if expanded and os.path.exists(expanded):
            return expanded
    return None


def dpkg_search(path: str) -> str | None:
    dpkg_query = which("dpkg-query") or "/usr/bin/dpkg-query"
    if not os.path.exists(dpkg_query):
        return None
    result = run_cmd([dpkg_query, "-S", path])
    if result.returncode != 0:
        return None
    line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    if ":" not in line:
        return None
    packages = line.split(":", 1)[0].strip()
    package = packages.split(",")[0].strip()
    if ":" in package:
        package = package.split(":", 1)[0]
    return package or None


def dpkg_status(package: str) -> tuple[str, str]:
    dpkg_query = which("dpkg-query") or "/usr/bin/dpkg-query"
    result = run_cmd(
        [dpkg_query, "-W", "-f=${Essential}\t${Priority}", package]
    )
    if result.returncode != 0:
        return "", ""
    essential, _, priority = result.stdout.strip().partition("\t")
    return essential.strip().lower(), priority.strip().lower()


def package_rdepends(package: str) -> list[str]:
    apt_cache = which("apt-cache") or "/usr/bin/apt-cache"
    if not os.path.exists(apt_cache):
        return []
    result = run_cmd(
        [apt_cache, "rdepends", "--installed", "--no-recommends", "--no-suggests", package],
        timeout=30,
    )
    if result.returncode != 0:
        return []
    deps: list[str] = []
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if not line or line.endswith(":") or line == package:
            continue
        if line.startswith("Reverse Depends"):
            continue
        if PACKAGE_NAME_RE.match(line) and line != package:
            deps.append(line)
    # Preserve order, drop duplicates
    seen: set[str] = set()
    unique: list[str] = []
    for item in deps:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def is_protected_package(package: str, essential: str = "", priority: str = "") -> bool:
    if essential == "yes":
        return True
    if priority == "required":
        return True
    if package in PROTECTED_PACKAGES:
        return True
    return any(package == prefix or package.startswith(prefix + "-") for prefix in PROTECTED_PREFIXES)


def find_appimage_path(meta: DesktopMeta) -> str | None:
    tokens = split_exec(meta.exec_line or "")
    binary = first_real_binary(tokens)
    for candidate in (
        expand_path(binary) if binary else None,
        expand_path(meta.executable),
        expand_path(meta.try_exec),
    ):
        if candidate and (is_appimage(candidate) or looks_like_appimage_path(candidate)):
            return candidate
    extra_path = meta.extra.get("X-AppImageLauncher-Dest") or meta.extra.get("X-AppImage-Name")
    if extra_path:
        expanded = expand_path(extra_path)
        if expanded and os.path.exists(expanded):
            return expanded
    for token in tokens:
        if looks_like_appimage_path(token):
            return expand_path(token)
    return None


def related_appimage_desktops(appimage_path: str, extra: Iterable[str] = ()) -> list[str]:
    found: list[str] = []
    search_dirs = [
        Path.home() / ".local/share/applications",
    ]
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        search_dirs.insert(0, Path(xdg) / "applications")
    needle = os.path.abspath(appimage_path)
    for directory in search_dirs:
        if not directory.is_dir():
            continue
        for desktop in directory.glob("*.desktop"):
            try:
                text = desktop.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if needle in text or os.path.basename(needle) in text:
                found.append(str(desktop))
    for item in extra:
        if item and item not in found:
            found.append(item)
    return found


def is_protected_file(path: str) -> bool:
    real = os.path.abspath(os.path.realpath(path))
    if real in ("/", "/home", str(Path.home())):
        return True
    return any(real == prefix.rstrip("/") or real.startswith(prefix) for prefix in PROTECTED_FILE_PREFIXES)


def result_error(summary: str, reason: str = "error") -> InspectResult:
    return InspectResult(
        ok=False,
        kind=KIND_ERROR,
        blocked=True,
        reason=reason,
        summary=summary,
    )


def inspect_desktop(meta: DesktopMeta) -> InspectResult:
    name = meta.name or meta.app_id
    if desktop_is_snap(meta):
        return InspectResult(
            ok=False,
            kind=KIND_SNAP,
            blocked=True,
            reason="snap_unsupported",
            name=name,
            app_id=meta.app_id,
            desktop_file=meta.filename,
            exec_line=meta.exec_line,
            summary=f"{name} 是 Snap 应用，当前版本不支持卸载。",
        )
    if desktop_is_flatpak(meta):
        return InspectResult(
            ok=False,
            kind=KIND_FLATPAK,
            blocked=True,
            reason="flatpak_unsupported",
            name=name,
            app_id=meta.app_id,
            desktop_file=meta.filename,
            exec_line=meta.exec_line,
            summary=f"{name} 是 Flatpak 应用，当前版本不支持卸载。",
        )
    if desktop_is_appimage(meta):
        appimage = find_appimage_path(meta)
        paths = []
        if appimage:
            paths.append(appimage)
        if meta.filename and os.path.abspath(meta.filename).startswith(str(Path.home())):
            paths.append(meta.filename)
        if appimage:
            for related in related_appimage_desktops(appimage, paths):
                if related not in paths:
                    paths.append(related)
        if not paths:
            return InspectResult(
                ok=False,
                kind=KIND_APPIMAGE,
                blocked=True,
                reason="appimage_missing",
                name=name,
                app_id=meta.app_id,
                desktop_file=meta.filename,
                exec_line=meta.exec_line,
                summary=f"找到了 {name} 的 AppImage 桌面项，但没有对应的 AppImage 文件。",
            )
        return InspectResult(
            ok=True,
            kind=KIND_APPIMAGE,
            name=name,
            app_id=meta.app_id,
            desktop_file=meta.filename,
            exec_line=meta.exec_line,
            paths=paths,
            action="trash",
            summary=f"将把 {name} 的 AppImage 和桌面快捷方式移入回收站，之后可以从回收站还原。",
        )

    binary = resolve_binary(meta)
    package = None
    if meta.filename:
        package = dpkg_search(meta.filename)
    if not package and binary:
        package = dpkg_search(binary)

    if package:
        if not PACKAGE_NAME_RE.match(package):
            return result_error(f"包名无效：{package}", "invalid_package")
        essential, priority = dpkg_status(package)
        rdepends = package_rdepends(package)
        protected = is_protected_package(package, essential, priority)
        if not protected:
            protected = any(is_protected_package(dep) for dep in rdepends)
        if protected:
            return InspectResult(
                ok=False,
                kind=KIND_SYSTEM,
                blocked=True,
                reason="system_package",
                name=name,
                app_id=meta.app_id,
                package=package,
                desktop_file=meta.filename,
                exec_line=meta.exec_line,
                rdepends=rdepends,
                summary=f"{name} 属于系统包 {package}，已阻止卸载以免损坏桌面。",
            )
        warning = ""
        if rdepends:
            preview = ", ".join(rdepends[:8])
            warning = f" 可能同时影响：{preview}。"
        return InspectResult(
            ok=True,
            kind=KIND_DEB,
            name=name,
            app_id=meta.app_id,
            package=package,
            desktop_file=meta.filename,
            exec_line=meta.exec_line,
            rdepends=rdepends,
            action="pkcon-remove",
            summary=f"将用包管理器卸载 {name}（软件包 {package}）。这不能从回收站还原。{warning}".strip(),
        )

    return InspectResult(
        ok=False,
        kind=KIND_UNKNOWN,
        blocked=True,
        reason="unrecognized",
        name=name,
        app_id=meta.app_id,
        desktop_file=meta.filename,
        exec_line=meta.exec_line,
        summary=f"无法识别 {name} 的安装来源（不是 apt/dpkg 或 AppImage）。",
    )


def inspect_app_id(app_id: str) -> InspectResult:
    try:
        meta = load_desktop_from_app_id(app_id)
    except (OSError, ValueError) as exc:
        return result_error(str(exc), "desktop_not_found")
    return inspect_desktop(meta)


def inspect_desktop_path(path: str) -> InspectResult:
    if not os.path.isfile(path):
        return result_error(f"找不到桌面文件：{path}", "desktop_not_found")
    return inspect_desktop(parse_desktop_file(path))


def uri_to_path(uri: str) -> str:
    if uri.startswith("file://"):
        from urllib.parse import unquote, urlparse

        parsed = urlparse(uri)
        return unquote(parsed.path)
    return uri


def inspect_uri(uri: str) -> InspectResult:
    path = os.path.abspath(uri_to_path(uri))
    if not os.path.exists(path):
        return result_error(f"找不到文件：{path}", "missing_file")
    if is_protected_file(path) and not path.endswith(".desktop"):
        return InspectResult(
            ok=False,
            kind=KIND_SYSTEM,
            blocked=True,
            reason="protected_path",
            name=os.path.basename(path),
            paths=[path],
            summary=f"拒绝删除系统路径：{path}",
        )
    if path.endswith(".desktop"):
        return inspect_desktop_path(path)
    if is_appimage(path) or looks_like_appimage_path(path):
        paths = [path]
        for related in related_appimage_desktops(path):
            if related not in paths:
                paths.append(related)
        return InspectResult(
            ok=True,
            kind=KIND_APPIMAGE,
            name=os.path.basename(path),
            paths=paths,
            action="trash",
            summary=f"将把 {os.path.basename(path)} 及相关桌面项移入回收站。",
        )
    return InspectResult(
        ok=True,
        kind=KIND_FILE,
        name=os.path.basename(path),
        paths=[path],
        action="trash",
        summary=f"将把 {os.path.basename(path)} 移入系统回收站，之后可以还原。",
    )


def trash_paths(paths: list[str]) -> InspectResult:
    trashed: list[str] = []
    for path in paths:
        if is_protected_file(path):
            return InspectResult(
                ok=False,
                kind=KIND_SYSTEM,
                blocked=True,
                reason="protected_path",
                paths=trashed,
                summary=f"拒绝删除系统路径：{path}",
            )
        try:
            _trash_one_path(path)
        except Exception as exc:  # noqa: BLE001 - surface any backend failure
            return InspectResult(
                ok=False,
                kind=KIND_ERROR,
                blocked=True,
                reason="trash_failed",
                paths=trashed,
                summary=f"无法移入回收站：{path}（{exc}）",
            )
        trashed.append(path)
    kind = KIND_APPIMAGE if any(
        is_appimage(p) or p.endswith(".desktop") for p in paths
    ) else KIND_FILE
    return InspectResult(
        ok=True,
        kind=kind,
        paths=trashed,
        action="trashed",
        summary=f"已移入回收站：{', '.join(os.path.basename(p) for p in trashed)}",
    )


def _trash_one_path(path: str) -> None:
    if _trash_with_gio(path):
        return
    _trash_freedesktop(path)


def _trash_with_gio(path: str) -> bool:
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
    except (ImportError, ValueError):
        gio = which("gio")
        if not gio:
            return False
        result = run_cmd([gio, "trash", path])
        return result.returncode == 0

    try:
        Gio.File.new_for_path(path).trash(None)
        return True
    except GLib.Error:
        return False


def _trash_freedesktop(path: str) -> None:
    from datetime import datetime
    from urllib.parse import quote

    data_home = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))
    trash_root = Path(data_home) / "Trash"
    files_dir = trash_root / "files"
    info_dir = trash_root / "info"
    files_dir.mkdir(parents=True, exist_ok=True)
    info_dir.mkdir(parents=True, exist_ok=True)

    original = os.path.abspath(path)
    name = os.path.basename(original) or "item"
    dest = files_dir / name
    index = 1
    while dest.exists() or (info_dir / f"{dest.name}.trashinfo").exists():
        dest = files_dir / f"{name}.{index}"
        index += 1

    info_path = info_dir / f"{dest.name}.trashinfo"
    info_path.write_text(
        "[Trash Info]\n"
        f"Path={quote(original, safe='/')}\n"
        f"DeletionDate={datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}\n",
        encoding="utf-8",
    )
    shutil.move(original, dest)


def remove_deb_package(package: str) -> InspectResult:
    if not PACKAGE_NAME_RE.match(package):
        return result_error(f"包名无效：{package}", "invalid_package")
    essential, priority = dpkg_status(package)
    if is_protected_package(package, essential, priority):
        return InspectResult(
            ok=False,
            kind=KIND_SYSTEM,
            blocked=True,
            reason="system_package",
            package=package,
            summary=f"已阻止卸载系统包 {package}。",
        )

    pkcon = which("pkcon") or "/usr/bin/pkcon"
    if os.path.exists(pkcon):
        result = run_cmd([pkcon, "--noninteractive", "remove", package], timeout=300)
        if result.returncode == 0:
            return InspectResult(
                ok=True,
                kind=KIND_DEB,
                package=package,
                action="removed",
                summary=f"已卸载软件包 {package}。",
            )
        # PackageKit may be missing even if pkcon exists.
        if "PackageKit" not in (result.stderr + result.stdout):
            return InspectResult(
                ok=False,
                kind=KIND_DEB,
                blocked=True,
                reason="pkcon_failed",
                package=package,
                summary=(result.stderr or result.stdout).strip() or f"卸载 {package} 失败。",
            )

    pkexec = which("pkexec") or "/usr/bin/pkexec"
    apt_get = which("apt-get") or "/usr/bin/apt-get"
    if not os.path.exists(pkexec) or not os.path.exists(apt_get):
        return result_error("找不到 pkcon 或 pkexec/apt-get，无法卸载 deb 包。", "no_uninstaller")
    result = run_cmd(
        [pkexec, apt_get, "remove", "-y", package],
        timeout=300,
    )
    if result.returncode != 0:
        return InspectResult(
            ok=False,
            kind=KIND_DEB,
            blocked=True,
            reason="apt_failed",
            package=package,
            summary=(result.stderr or result.stdout).strip() or f"卸载 {package} 失败。",
        )
    return InspectResult(
        ok=True,
        kind=KIND_DEB,
        package=package,
        action="removed",
        summary=f"已卸载软件包 {package}。",
    )


def apply_inspect(inspected: InspectResult) -> InspectResult:
    if not inspected.ok or inspected.blocked:
        return inspected
    if inspected.kind in {KIND_FILE, KIND_APPIMAGE}:
        return trash_paths(inspected.paths)
    if inspected.kind == KIND_DEB and inspected.package:
        return remove_deb_package(inspected.package)
    return result_error("没有可执行的卸载动作。", "no_action")


def inspect_request(args: argparse.Namespace) -> InspectResult:
    if args.app_id:
        return inspect_app_id(args.app_id)
    if args.desktop_file:
        return inspect_desktop_path(args.desktop_file)
    if args.uri:
        return inspect_uri(args.uri)
    return result_error("缺少 --app-id、--desktop-file 或 --uri。", "missing_target")


def emit(result: InspectResult) -> int:
    log(
        f"{result.kind} ok={result.ok} blocked={result.blocked} "
        f"app_id={result.app_id} package={result.package} summary={result.summary}"
    )
    payload = json.dumps(result.to_dict(), ensure_ascii=False) + "\n"
    # GJS promisify 在旧代码里会丢掉 stdout，只读到 stderr。两边都写纯 JSON。
    sys.stdout.write(payload)
    sys.stdout.flush()
    sys.stderr.write(payload)
    sys.stderr.flush()
    return 0 if result.ok and not result.blocked else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ubuntu Dock Trash helper")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "apply"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--app-id")
        cmd.add_argument("--desktop-file")
        cmd.add_argument("--uri")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    log(
        f"{args.command} app_id={args.app_id} "
        f"desktop_file={args.desktop_file} uri={args.uri}"
    )
    inspected = inspect_request(args)
    if args.command == "inspect":
        return emit(inspected)
    return emit(apply_inspect(inspected))


if __name__ == "__main__":
    sys.exit(main())
