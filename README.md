# Ubuntu Dock Trash

把应用网格或 Ubuntu Dock 里的应用图标拖进 Dock 垃圾桶，即可卸载 apt/dpkg 应用，或把已集成到应用列表里的 AppImage 移入系统回收站。

目标系统：**Ubuntu 26.04 LTS**（GNOME 50 + Wayland）。

这是一个 GNOME Shell 扩展（UUID：`dock-trash@ubuntu-trash`），不是独立窗口程序。应用网格里的图标只能在 `gnome-shell` 内部拖放，普通 GTK 窗口接不到这些图标。

## 能做什么 / 不能做什么

**可以**

* 从**应用网格**或 **Ubuntu Dock** 拖应用图标到 Dock 垃圾桶
* 卸载 apt / dpkg 安装的应用（确认后 `pkcon remove` 或 `pkexec apt-get remove`）
* 删除已经出现在应用列表里的 AppImage（连同对应 `.desktop` 进系统回收站，可还原）
* 拒绝 Snap、Flatpak、系统关键包，避免误卸桌面

**不可以（Wayland 限制，不是黑名单）**

从「文件」窗口、桌面、下载目录把**普通文件**或 **`.AppImage` 文件**拖到 Dock 垃圾桶，**不会生效**。

这是 Ubuntu Dock 在 Wayland 上的长期限制：跨程序拖放到 Shell 时，扩展拿不到文件路径。上游说明见 [dash-to-dock#1907](https://github.com/micheleg/dash-to-dock/issues/1907)。

这类文件请用「文件」应用自带的回收站删除。

若 AppImage 已经集成进应用网格（有启动器图标），请拖**网格里的图标**，不要拖下载目录里的那个文件。

## 依赖

* Ubuntu 26.04 Desktop（GNOME Shell 50）
* `gnome-shell`、`gnome-extensions`（系统自带）
* Python 3
* 卸载 deb 需要其一：
  * `pkcon`（包名 `packagekit-tools`）
  * 或 `pkexec` + `apt-get`（系统一般已有）

可选：

```bash
sudo apt install packagekit-tools
```

## 下载

发布到 GitHub 后：

```bash
git clone https://github.com/<你的用户名>/<仓库名>.git
cd <仓库名>
```

或在 GitHub 仓库页点击 **Code → Download ZIP**，解压后进入目录。

## 安装

在仓库根目录执行：

```bash
chmod +x install.sh
./install.sh
```

脚本会：

1. 打包扩展（`gnome-extensions pack`）
2. 安装到 `~/.local/share/gnome-shell/extensions/dock-trash@ubuntu-trash`
3. 把 UUID 写入 `org.gnome.shell enabled-extensions`

## 启动（务必注销一次）

Ubuntu 26.04 是 Wayland。GNOME Shell **只在登录时**扫描用户扩展。

* 安装后如果马上执行 `gnome-extensions enable dock-trash@ubuntu-trash`，可能提示「扩展不存在」
* 改过扩展 JS 之后，`disable` / `enable` **不会**重新加载模块
* **请注销并重新登录**

登录后扩展会按 `enabled-extensions` 自动启用。确认：

```bash
gnome-extensions info dock-trash@ubuntu-trash
```

`状态` 应为 `ACTIVE`，`已启用` 应为 `是`。

也可在「扩展」应用里打开 **Ubuntu Dock Trash**。

若仍未出现：

```bash
gsettings get org.gnome.shell disable-user-extensions
# 应为 false
gsettings get org.gnome.shell allow-extension-installation
# 应为 true
```

## 使用

1. 打开应用网格（或看左侧 Ubuntu Dock）
2. 把应用图标拖到 Dock 上的垃圾桶，图标会缩小淡出
3. 在确认框里选择「卸载」或「取消」
4. 卸载 deb 时可能弹出 polkit 密码框
5. 应用网格会留着，可以继续拖下一个

确认框示例：

* 标题：`卸载 RustDesk？`
* 正文：`将卸载软件 rustdesk，卸载之后不能从回收站还原！`

## 卸载本扩展

```bash
gnome-extensions disable dock-trash@ubuntu-trash
gnome-extensions uninstall dock-trash@ubuntu-trash
```

然后注销再登录。也可手动删除：

```bash
rm -rf ~/.local/share/gnome-shell/extensions/dock-trash@ubuntu-trash
```

并从 `gsettings get org.gnome.shell enabled-extensions` 里去掉该 UUID。

## 更新

```bash
git pull
./install.sh
```

然后**注销再登录**。只跑 `gnome-extensions enable` 不够。

#
