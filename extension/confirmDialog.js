import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';
import St from 'gi://St';

import * as ModalDialog from 'resource:///org/gnome/shell/ui/modalDialog.js';
import {gettext as _} from 'resource:///org/gnome/shell/extensions/extension.js';

export const ConfirmDialog = GObject.registerClass(
class ConfirmDialog extends ModalDialog.ModalDialog {
    _init({title, body, warning, confirmLabel, onConfirm}) {
        super._init({styleClass: 'prompt-dialog'});

        const box = new St.BoxLayout({
            orientation: Clutter.Orientation.VERTICAL,
            style_class: 'message-dialog-content',
        });

        box.add_child(new St.Label({
            text: title,
            style_class: 'prompt-dialog-headline dock-trash-dialog-title',
        }));

        const bodyLabel = new St.Label({
            text: body,
            style_class: 'prompt-dialog-description dock-trash-dialog-body',
        });
        bodyLabel.clutter_text.line_wrap = true;
        box.add_child(bodyLabel);

        if (warning) {
            const warnLabel = new St.Label({
                text: warning,
                style_class: 'prompt-dialog-description dock-trash-dialog-warning',
            });
            warnLabel.clutter_text.line_wrap = true;
            box.add_child(warnLabel);
        }

        this.contentLayout.add_child(box);

        this.addButton({
            label: _('取消'),
            action: () => this.close(),
            key: Clutter.KEY_Escape,
        });
        this.addButton({
            label: confirmLabel || _('确认'),
            action: () => {
                this.close();
                onConfirm?.();
            },
            default: true,
        });
    }
});

export const MessageDialog = GObject.registerClass(
class MessageDialog extends ModalDialog.ModalDialog {
    _init({title, body}) {
        super._init({styleClass: 'prompt-dialog'});

        const box = new St.BoxLayout({
            orientation: Clutter.Orientation.VERTICAL,
            style_class: 'message-dialog-content',
        });
        box.add_child(new St.Label({
            text: title,
            style_class: 'prompt-dialog-headline dock-trash-dialog-title',
        }));
        const bodyLabel = new St.Label({
            text: body,
            style_class: 'prompt-dialog-description dock-trash-dialog-body',
        });
        bodyLabel.clutter_text.line_wrap = true;
        box.add_child(bodyLabel);
        this.contentLayout.add_child(box);

        this.addButton({
            label: _('确定'),
            action: () => this.close(),
            default: true,
            key: Clutter.KEY_Escape,
        });
    }
});
