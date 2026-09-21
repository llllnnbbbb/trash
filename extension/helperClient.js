import Gio from 'gi://Gio';

Gio._promisify(Gio.Subprocess.prototype, 'communicate_utf8_async');

export class HelperClient {
    constructor(helperPath) {
        this._helperPath = helperPath;
    }

    inspectApp(appId) {
        return this._run(['inspect', '--app-id', appId]);
    }

    inspectUri(uri) {
        return this._run(['inspect', '--uri', uri]);
    }

    applyApp(appId) {
        return this._run(['apply', '--app-id', appId]);
    }

    applyUri(uri) {
        return this._run(['apply', '--uri', uri]);
    }

    async _run(args) {
        console.log(`dock-trash: helper ${args.join(' ')}`);
        const launcher = Gio.Subprocess.new(
            ['/usr/bin/python3', '-W', 'ignore', this._helperPath, ...args],
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
        );
        // GJS promisify 去掉了 gboolean，返回值是 [stdout, stderr]
        const output = await launcher.communicate_utf8_async(null, null);
        const stdout = output.length === 3 ? output[1] : output[0];
        const stderr = output.length === 3 ? output[2] : output[1];
        if (stderr?.trim())
            console.log(`dock-trash: helper stderr\n${stderr.trim()}`);
        if (stdout?.trim())
            console.log(`dock-trash: helper stdout\n${stdout.trim()}`);
        return this._parsePayload(stdout, stderr);
    }

    _parsePayload(stdout, stderr) {
        const parsed = this._extractJson(stdout) || this._extractJson(stderr);
        if (parsed)
            return parsed;
        const message = (stdout || '').trim() || (stderr || '').trim() || '助手没有返回结果';
        throw new Error(`助手返回了无效 JSON：${message}`);
    }

    _extractJson(text) {
        if (!text)
            return null;
        try {
            return JSON.parse(text);
        } catch (error) {
            console.warn(`dock-trash: stdout 不是纯 JSON，尝试提取最后一行：${error}`);
        }
        const lines = text.split(/\n/).map(line => line.trim()).filter(Boolean);
        for (let i = lines.length - 1; i >= 0; i--) {
            const line = lines[i];
            if (!line.startsWith('{') || !line.endsWith('}'))
                continue;
            try {
                return JSON.parse(line);
            } catch (error) {
                console.warn(`dock-trash: 跳过无法解析的行：${error}`);
            }
        }
        return null;
    }
}
