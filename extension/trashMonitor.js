import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';

import * as DND from 'resource:///org/gnome/shell/ui/dnd.js';
import * as Dash from 'resource:///org/gnome/shell/ui/dash.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const DOCK_UUIDS = [
    'ubuntu-dock@ubuntu.com',
    'dash-to-dock@micxgx.gmail.com',
];

export function isTrashApp(app) {
    if (!app)
        return false;
    if (app.isTrash)
        return true;
    const id = app.get_id?.() ?? '';
    if (id.startsWith('location:trash:'))
        return true;
    const uri = app.location?.get_uri?.() ??
        app.appInfo?.location?.get_uri?.() ??
        '';
    return uri.startsWith('trash:');
}

export function getAppFromSource(source) {
    if (!source)
        return null;
    if (source.app && typeof source.app.get_id === 'function')
        return source.app;
    if (source instanceof Dash.DashIcon)
        return source.app;
    return null;
}

export function getUrisFromSource(source) {
    if (!source)
        return [];
    const uris = [];
    if (typeof source.uri === 'string')
        uris.push(source.uri);
    if (typeof source.file === 'string')
        uris.push(source.file);
    if (Array.isArray(source.uris))
        uris.push(...source.uris);
    if (Array.isArray(source.files)) {
        for (const file of source.files) {
            if (typeof file === 'string')
                uris.push(file);
            else if (file?.get_uri)
                uris.push(file.get_uri());
        }
    }
    return uris.filter(Boolean);
}

function actorChain(actor) {
    const chain = [];
    while (actor) {
        chain.push(actor);
        actor = actor.get_parent();
    }
    return chain;
}

export function findTrashActorFrom(actor) {
    for (const current of actorChain(actor)) {
        if (current._dockTrashDrop)
            return current;
        const app = current._delegate?.app;
        if (isTrashApp(app))
            return current;
    }
    return null;
}

export class TrashDropMonitor {
    constructor({onDropApp, onDropUris}) {
        this._onDropApp = onDropApp;
        this._onDropUris = onDropUris;
        this._hovered = null;
        this._source = null;
        this._shrunkActor = null;
        this._origOpacity = 255;
        this._fallbackItems = [];
        this._wrappedActors = [];
        this._dragMonitor = {
            dragMotion: event => this._dragMotion(event),
            dragDrop: event => this._dragDrop(event),
        };
        DND.addDragMonitor(this._dragMonitor);
        this._tries = 0;
        this._injectId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 600, () => {
            this._wrapKnownTrash();
            if (this._wrappedActors.length) {
                this._injectId = 0;
                return GLib.SOURCE_REMOVE;
            }
            this._tries += 1;
            if (this._tries < 5)
                return GLib.SOURCE_CONTINUE;
            this._maybeInjectFallback();
            this._injectId = 0;
            return GLib.SOURCE_REMOVE;
        });
    }

    destroy() {
        if (this._injectId) {
            GLib.source_remove(this._injectId);
            this._injectId = 0;
        }
        DND.removeDragMonitor(this._dragMonitor);
        this._clearHover(true);
        this._unwrapAll();
        for (const item of this._fallbackItems)
            item.destroy();
        this._fallbackItems = [];
        this._source = null;
    }

    _dragMotion(event) {
        const trash = findTrashActorFrom(event.targetActor);
        this._source = event.source ?? this._source;
        if (trash)
            this._wrapDelegate(trash);

        if (!trash || !this._hasDroppable(this._source)) {
            this._clearHover(true);
            return DND.DragMotionResult.CONTINUE;
        }
        this._setHover(trash, event.dragActor);
        return DND.DragMotionResult.MOVE_DROP;
    }

    _dragDrop(event) {
        const trash = findTrashActorFrom(event.targetActor);
        if (trash)
            this._wrapDelegate(trash);
        // 必须 CONTINUE，让 Shell 走 acceptDrop 并销毁拖拽图标。
        // 返回 SUCCESS 会卡住抓取，图标会一直粘在指针上。
        this._clearHover(false);
        this._source = null;
        this._shrunkActor = null;
        return DND.DragDropResult.CONTINUE;
    }

    _accept(source) {
        const app = getAppFromSource(source);
        if (app && !isTrashApp(app) && !app.is_window_backed?.()) {
            this._onDropApp(app);
            return true;
        }
        const uris = getUrisFromSource(source);
        if (uris.length) {
            this._onDropUris(uris);
            return true;
        }
        return false;
    }

    _hasDroppable(source) {
        const app = getAppFromSource(source);
        if (app && !isTrashApp(app) && !app.is_window_backed?.())
            return true;
        return getUrisFromSource(source).length > 0;
    }

    _setHover(actor, dragActor) {
        if (this._hovered !== actor) {
            this._hovered?.remove_style_class_name('dock-trash-drop-hover');
            this._hovered = actor;
            actor.add_style_class_name('dock-trash-drop-hover');
        }
        if (!dragActor || this._shrunkActor === dragActor)
            return;
        this._restoreDragActor();
        this._shrunkActor = dragActor;
        this._origOpacity = dragActor.opacity;
        dragActor.remove_all_transitions?.();
        dragActor.set_pivot_point(0.5, 0.5);
        dragActor.ease({
            scale_x: 0.2,
            scale_y: 0.2,
            opacity: 0,
            duration: 140,
            mode: Clutter.AnimationMode.EASE_IN_QUAD,
        });
    }

    _restoreDragActor() {
        const actor = this._shrunkActor;
        this._shrunkActor = null;
        if (!actor)
            return;
        try {
            actor.remove_all_transitions?.();
            actor.ease({
                scale_x: 1,
                scale_y: 1,
                opacity: this._origOpacity ?? 255,
                duration: 100,
                mode: Clutter.AnimationMode.EASE_OUT_QUAD,
            });
        } catch (error) {
            console.debug(`dock-trash: restore drag actor ${error}`);
        }
    }

    _clearHover(restoreActor) {
        this._hovered?.remove_style_class_name('dock-trash-drop-hover');
        this._hovered = null;
        if (restoreActor)
            this._restoreDragActor();
        else
            this._shrunkActor = null;
    }

    _wrapDelegate(actor) {
        const original = actor?._delegate;
        if (!original || original._dockTrashHooked)
            return;

        // DashIcon 的 _delegate 就是它自己，必须带 .icon / .app。
        // 换成普通对象后，Ubuntu Dock 会认为垃圾桶不是合法图标，反复调
        // _adjustIconSize()，Dock 宽度抖动，「文件」侧栏一直闪。
        const prevOver = original.handleDragOver?.bind(original);
        const prevDrop = original.acceptDrop?.bind(original);
        original._dockTrashHooked = true;
        original._dockTrashPrevOver = prevOver;
        original._dockTrashPrevDrop = prevDrop;
        original.handleDragOver = (...args) => {
            if (this._hasDroppable(args[0]))
                return DND.DragMotionResult.MOVE_DROP;
            return prevOver?.(...args) ?? DND.DragMotionResult.CONTINUE;
        };
        original.acceptDrop = (...args) => {
            if (this._accept(args[0]))
                return true;
            return prevDrop?.(...args) ?? false;
        };
        this._wrappedActors.push(original);
    }

    _unwrapAll() {
        for (const original of this._wrappedActors) {
            if (original._dockTrashPrevOver)
                original.handleDragOver = original._dockTrashPrevOver;
            else
                delete original.handleDragOver;
            if (original._dockTrashPrevDrop)
                original.acceptDrop = original._dockTrashPrevDrop;
            else
                delete original.acceptDrop;
            delete original._dockTrashHooked;
            delete original._dockTrashPrevOver;
            delete original._dockTrashPrevDrop;
        }
        this._wrappedActors = [];
    }

    _collectTrash(actor, out, depth = 0) {
        if (!actor || depth > 16)
            return;
        if (actor._dockTrashDrop || (actor._delegate?.app && isTrashApp(actor._delegate.app)))
            out.push(actor);
        const children = actor.get_children?.() ?? [];
        for (const child of children)
            this._collectTrash(child, out, depth + 1);
    }

    _wrapKnownTrash() {
        const found = [];
        this._collectTrash(Main.overview?.dash, found);
        this._collectTrash(Main.uiGroup, found);
        this._collectTrash(Main.layoutManager?.uiGroup, found);
        for (const actor of found)
            this._wrapDelegate(actor);
        return found.length > 0;
    }

    _searchTrash(actor, depth = 0) {
        if (!actor || depth > 16)
            return null;
        if (actor._dockTrashDrop)
            return actor;
        if (actor._delegate?.app && isTrashApp(actor._delegate.app))
            return actor;
        const children = actor.get_children?.() ?? [];
        for (const child of children) {
            const found = this._searchTrash(child, depth + 1);
            if (found)
                return found;
        }
        return null;
    }

    _existingTrashFound() {
        return this._wrapKnownTrash();
    }

    _maybeInjectFallback() {
        if (this._fallbackItems.length || this._wrapKnownTrash())
            return;
        const dash = Main.overview?.dash;
        if (!dash?._box)
            return;
        const item = this._createFallbackItem(dash.iconSize ?? 48);
        dash._box.add_child(item);
        this._fallbackItems.push(item);
        this._wrapDelegate(item);
    }

    _createFallbackItem(iconSize) {
        const icon = new St.Icon({
            icon_name: 'user-trash-full-symbolic',
            icon_size: iconSize,
            fallback_icon_name: 'user-trash-symbolic',
        });
        const button = new St.Button({
            style_class: 'dash-item-container dock-trash-fallback',
            child: icon,
            can_focus: true,
            reactive: true,
            track_hover: true,
        });
        button._dockTrashDrop = true;
        button._delegate = {
            handleDragOver: () => DND.DragMotionResult.MOVE_DROP,
            acceptDrop: source => this._accept(source),
        };
        button.connect('clicked', () => {
            Main.overview.hide();
            Gio.AppInfo.launch_default_for_uri(
                'trash:///',
                global.create_app_launch_context(0, -1)
            );
        });
        return button;
    }
}
