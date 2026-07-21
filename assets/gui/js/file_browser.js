(() => {
    "use strict";
    const root = window.Palsitter = window.Palsitter || {};
    const entries = {};
    let doubleClickRow = null;
    const platformSep = platform => platform === "windows" ? "\\" : "/";
    const isSep = (character, platform) => platform === "windows" ? character === "\\" || character === "/" : character === "/";
    const stripMatchingQuotes = value => {
        const text = String(value || "").trim();
        if (text.length >= 2 && ((text[0] === '"' && text.at(-1) === '"') || (text[0] === "'" && text.at(-1) === "'"))) return text.slice(1, -1).trim();
        return text;
    };
    const normalizeSlash = (value, platform) => platform === "windows" ? String(value || "").replace(/\//g, "\\") : String(value || "");
    const isWindowsRoot = value => /^[a-zA-Z]:\\$/.test(value) || /^\\\\[^\\]+\\[^\\]+\\?$/.test(value);
    const stripTrailingSep = (value, platform) => {
        let text = normalizeSlash(value, platform);
        while (text.length > 1 && isSep(text.at(-1), platform)) {
            if (platform === "linux" && text === "/") break;
            if (platform === "windows" && isWindowsRoot(text)) break;
            text = text.slice(0, -1);
        }
        return text;
    };
    const compareText = (value, platform) => {
        const normalized = stripTrailingSep(value, platform);
        return platform === "windows" ? normalized.toLowerCase() : normalized;
    };
    const basename = (value, platform) => {
        const text = stripTrailingSep(value, platform);
        const slash = platform === "windows" ? Math.max(text.lastIndexOf("\\"), text.lastIndexOf("/")) : text.lastIndexOf("/");
        return slash >= 0 ? text.slice(slash + 1) : text;
    };
    const dirname = (value, platform) => {
        const text = stripTrailingSep(value, platform);
        const slash = platform === "windows" ? Math.max(text.lastIndexOf("\\"), text.lastIndexOf("/")) : text.lastIndexOf("/");
        if (slash < 0) return "";
        if (platform === "linux" && slash === 0) return "/";
        if (platform === "windows" && slash === 2 && /^[a-zA-Z]:/.test(text)) return text.slice(0, 3);
        return text.slice(0, slash);
    };
    const hasSeparator = (value, platform) => Array.from(String(value || "")).some(character => isSep(character, platform));
    const isAbsolute = (value, platform) => {
        const text = normalizeSlash(value, platform);
        return platform === "windows" ? /^[a-zA-Z]:\\/.test(text) || text.startsWith("\\\\") : text.startsWith("/");
    };
    const expandHome = (value, entry) => {
        const text = String(value || "");
        if (!entry.homeDir || text[0] !== "~") return text;
        if (text === "~") return entry.homeDir;
        if (text.length > 1 && isSep(text[1], entry.platform)) {
            const rest = text.slice(2);
            const home = stripTrailingSep(entry.homeDir, entry.platform);
            return rest ? home + platformSep(entry.platform) + rest : home;
        }
        return text;
    };
    const unique = values => {
        const seen = new Set();
        const result = [];
        for (const value of values || []) {
            const text = String(value || "");
            if (!text || seen.has(text)) continue;
            seen.add(text);
            result.push(text);
        }
        return result;
    };
    const filterValues = entry => {
        const raw = stripMatchingQuotes(entry.input?.value);
        const expandedRaw = expandHome(raw, entry);
        const inputKey = compareText(expandedRaw, entry.platform);
        const currentDirKey = compareText(entry.currentDir || "", entry.platform);
        const basenameOnly = raw !== "" && !raw.startsWith("~") && !isAbsolute(expandedRaw, entry.platform) && !hasSeparator(raw, entry.platform);
        const baseKey = entry.platform === "windows" ? raw.toLowerCase() : raw;
        const matches = [];
        for (const value of entry.suggestions) {
            const base = basename(value, entry.platform);
            const item = {
                value,
                dir: /[\\/]$/.test(String(value || "")),
                base,
                parent: compareText(dirname(value, entry.platform), entry.platform),
                full: compareText(value, entry.platform),
            };
            const itemBase = entry.platform === "windows" ? base.toLowerCase() : base;
            if (!raw || (basenameOnly && item.parent === currentDirKey && itemBase.startsWith(baseKey)) || (!basenameOnly && item.full.startsWith(inputKey))) matches.push(item);
        }
        matches.sort((left, right) => left.dir !== right.dir ? (left.dir ? -1 : 1) : left.base.localeCompare(right.base, undefined, {sensitivity: entry.platform === "windows" ? "base" : "variant"}));
        return matches.slice(0, entry.maxOptions).map(item => item.value);
    };
    const render = inputId => {
        const entry = entries[inputId];
        if (!entry?.datalist) return;
        const values = filterValues(entry);
        const key = values.join("\n");
        if (key === entry.filteredKey) return;
        entry.filteredKey = key;
        entry.filtered = values;
        entry.datalist.replaceChildren(...values.map(value => {
            const option = document.createElement("option");
            option.value = value;
            return option;
        }));
    };
    const update = ({inputId, suggestions, version, ...meta}) => {
        const entry = entries[inputId];
        if (!entry) return;
        const nextVersion = Number(version || 0);
        if (nextVersion < Number(entry.version || 0)) return;
        entry.version = nextVersion;
        if (meta.currentDir !== undefined) entry.currentDir = meta.currentDir || "";
        if (meta.homeDir !== undefined) entry.homeDir = meta.homeDir || "";
        if (meta.platform !== undefined) entry.platform = meta.platform || "";
        if (meta.maxOptions !== undefined) entry.maxOptions = meta.maxOptions || 100;
        entry.suggestions = unique(suggestions);
        render(inputId);
    };
    const register = config => {
        const input = Array.from(document.querySelectorAll("input")).find(element => element.name === config.name);
        if (!input) return;
        entries[config.inputId]?.controller?.abort();
        const controller = new AbortController();
        const datalist = document.getElementById(config.datalistId) || document.createElement("datalist");
        datalist.id = config.datalistId;
        if (!datalist.isConnected) input.insertAdjacentElement("afterend", datalist);
        input.id = config.inputId;
        input.dataset.autocompleteId = config.inputId;
        input.setAttribute("list", config.datalistId);
        entries[config.inputId] = {input, datalist, controller, suggestions: [], platform: config.platform, maxOptions: config.maxOptions || 100, currentDir: config.currentDir || "", homeDir: config.homeDir || "", version: 0, filteredKey: "", filtered: []};
        input.addEventListener("input", () => render(config.inputId), {signal: controller.signal});
        input.addEventListener("keydown", event => {
            if (event.key !== "Enter") return;
            const bridge = document.querySelector("#pywebio-scope-browse_address_enter button");
            if (!bridge) return;
            event.preventDefault();
            bridge.click();
        }, {signal: controller.signal});
        input.addEventListener("blur", () => document.querySelector("#pywebio-scope-browse_address_check button")?.click(), {signal: controller.signal});
        update(config);
    };
    root.fileBrowser = {
        entries,
        register,
        update,
        cleanup({inputId}) {
            entries[inputId]?.controller?.abort();
            delete entries[inputId];
        },
        mountTable() {
            const table = document.querySelector("#pywebio-scope-browse_list");
            if (!table || table.dataset.doubleClickReady) return;
            table.dataset.doubleClickReady = "true";
            table.addEventListener("dblclick", event => {
                const row = event.target.closest(".ag-row[row-id]");
                if (!row || !table.contains(row)) return;
                event.preventDefault();
                doubleClickRow = row.getAttribute("row-id");
                document.querySelector("#pywebio-scope-browse_double_click button")?.click();
            });
        },
        doubleClickRow() { return doubleClickRow; },
    };
})();
