import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Shell from 'gi://Shell';

import * as AppFavorites from 'resource:///org/gnome/shell/ui/appFavorites.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as MessageTray from 'resource:///org/gnome/shell/ui/messageTray.js';
import {Extension, gettext as _} from 'resource:///org/gnome/shell/extensions/extension.js';

import {ConfirmDialog, MessageDialog} from './confirmDialog.js';
import {HelperClient} from './helperClient.js';
import {TrashDropMonitor, isTrashApp} from './trashMonitor.js';

export default class DockTrashExtension extends Extension {
    enable() {
        const helperPath = GLib.build_filenamev([this.path, 'helper', 'trash_helper.py']);
        console.log(`dock-trash: enable helper=${helperPath}`);
        this._helper = new HelperClient(helperPath);
        this._source = null;
        this._dialog = null;
        this._busy = false;
        this._applying = false;
        this._refreshId = 0;
        this._monitor = new TrashDropMonitor({
            onDropApp: app => this._handleApp(app),
            onDropUris: uris => this._handleUris(uris),
        });
    }

    disable() {
        if (this._refreshId) {
            GLib.source_remove(this._refreshId);
            this._refreshId = 0;
        }
        this._monitor?.destroy();
        this._monitor = null;
        this._dialog?.destroy();
        this._dialog = null;
        this._source?.destroy?.();
        this._source = null;
        this._helper = null;
        this._busy = false;
        this._applying = false;
    }

    _notify(title, body) {
        if (!this._source) {
            this._source = new MessageTray.Source({
                title: _('回收站'),
                iconName: 'user-trash-symbolic',
            });
            Main.messageTray.add(this._source);
        }
        const notification = new MessageTray.Notification({
            source: this._source,
            title,
            body: body || '',
        });
        this._source.addNotification(notification);
    }

    _closeDialog() {
        this._dialog?.destroy();
        this._dialog = null;
    }

    _showMessage(title, body) {
        this._closeDialog();
        this._dialog = new MessageDialog({title, body});
        this._dialog.connect('closed', () => {
            this._dialog = null;
        });
        this._dialog.open();
    }

    _blockedMessage(info) {
        switch (info.reason) {
        case 'snap_unsupported':
            return _('不支持 Snap 应用。');
        case 'flatpak_unsupported':
            return _('不支持 Flatpak 应用。');
        case 'system_package':
            return _('这是系统组件，不能卸载。');
        default:
            return info.summary || _('无法删除。');
        }
    }

    _dialogCopy(info, fallbackName) {
        const name = info.name || fallbackName;
        if (info.kind === 'deb') {
            const pkg = info.package || name;
            return {
                title: _('卸载 %s？').format(name),
                body: _('将卸载软件 %s，卸载之后不能从回收站还原！').format(pkg),
                confirmLabel: _('卸载'),
            };
        }
        return {
            title: _('删除 %s？').format(name),
            body: _('将移到回收站。'),
            confirmLabel: _('删除'),
        };
    }

    async _handleApp(app) {
        if (this._busy || !app || isTrashApp(app) || app.is_window_backed?.())
            return;
        const appId = app.get_id();
        if (!appId)
            return;
        console.log(`dock-trash: drop app ${app.get_name()} id=${appId}`);
        await this._inspectAndConfirm({
            title: app.get_name() || appId,
            inspect: () => this._helper.inspectApp(appId),
            apply: () => this._helper.applyApp(appId),
        });
    }

    async _handleUris(uris) {
        if (this._busy || !uris.length)
            return;
        const uri = uris[0];
        console.log(`dock-trash: drop uri ${uri}`);
        await this._inspectAndConfirm({
            title: GLib.filename_display_basename(Gio.File.new_for_uri(uri).get_path() || uri),
            inspect: () => this._helper.inspectUri(uri),
            apply: () => this._helper.applyUri(uri),
        });
    }

    _keepOverview() {
        if (!Main.overview.visible)
            Main.overview.show();
    }

    async _inspectAndConfirm({title, inspect, apply}) {
        this._busy = true;
        const stayInOverview = Main.overview.visible;
        try {
            const info = await inspect();
            console.log(`dock-trash: inspect ${JSON.stringify(info)}`);
            if (!info.ok || info.blocked) {
                this._showMessage(_('无法删除'), this._blockedMessage(info));
                this._busy = false;
                if (stayInOverview)
                    this._keepOverview();
                return;
            }
            this._closeDialog();
            const copy = this._dialogCopy(info, title);
            this._dialog = new ConfirmDialog({
                title: copy.title,
                body: copy.body,
                confirmLabel: copy.confirmLabel,
                onConfirm: () => this._apply(apply, info, stayInOverview),
            });
            this._dialog.connect('closed', () => {
                this._dialog = null;
                if (!this._applying)
                    this._busy = false;
                if (stayInOverview)
                    this._keepOverview();
            });
            this._dialog.open();
        } catch (error) {
            console.error(`dock-trash inspect failed: ${error}`);
            this._busy = false;
            this._showMessage(_('无法删除'), error.message || String(error));
            if (stayInOverview)
                this._keepOverview();
        }
    }

    async _apply(apply, info, stayInOverview = false) {
        this._busy = true;
        this._applying = true;
        try {
            const result = await apply();
            console.log(`dock-trash: apply ${JSON.stringify(result)}`);
            if (!result.ok || result.blocked) {
                this._showMessage(_('删除失败'), result.summary || _('没有完成。'));
                return;
            }
            const name = info?.name || '';
            this._notify(info?.kind === 'deb' ? _('已卸载 %s').format(name) : _('已删除 %s').format(name));
            this._refreshShell(info?.app_id);
            if (stayInOverview)
                this._keepOverview();
        } catch (error) {
            console.error(`dock-trash apply failed: ${error}`);
            this._showMessage(_('删除失败'), error.message || String(error));
        } finally {
            this._applying = false;
            this._busy = false;
            if (stayInOverview)
                this._keepOverview();
        }
    }

    _refreshShell(appId) {
        try {
            const favorites = AppFavorites.getAppFavorites();
            if (appId && favorites.isFavorite(appId))
                favorites.removeFavorite(appId);
        } catch (error) {
            console.warn(`dock-trash: favorites ${error}`);
        }

        const appSystem = Shell.AppSystem.get_default();
        try {
            appSystem.emit('installed-changed');
        } catch (error) {
            console.warn(`dock-trash: installed-changed ${error}`);
        }

        this._redisplay();
        if (this._refreshId)
            GLib.source_remove(this._refreshId);

        let tries = 0;
        this._refreshId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 400, () => {
            tries += 1;
            const stillThere = appId && appSystem.lookup_app(appId);
            this._redisplay();
            if (stillThere && tries < 20)
                return GLib.SOURCE_CONTINUE;
            this._refreshId = 0;
            return GLib.SOURCE_REMOVE;
        });
    }

    _redisplay() {
        try {
            Main.overview?.dash?._redisplay?.();
        } catch (error) {
            console.warn(`dock-trash: dash redisplay ${error}`);
        }
        try {
            const controls = Main.overview?._overview?.controls;
            const appDisplay = controls?._appDisplay ?? controls?.appDisplay;
            appDisplay?._redisplay?.();
        } catch (error) {
            console.warn(`dock-trash: appDisplay redisplay ${error}`);
        }
    }
}
