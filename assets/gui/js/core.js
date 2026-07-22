(() => {
    "use strict";

    const root = window.Palsitter = window.Palsitter || {};

    const resolve = path => {
        let value = root;
        for (const part of String(path || "").split(".")) {
            value = value && value[part];
        }
        if (typeof value !== "function") {
            throw new Error(`Unknown Palsitter client API: ${path}`);
        }
        return value;
    };

    root.invoke = (path, payload) => resolve(path)(payload || {});
    root.query = (path, payload) => resolve(path)(payload || {});

    let pageGeneration = 0;
    root.page = {
        begin({generation}) {
            pageGeneration = Number(generation || 0);
        },
        isCurrent(generation) {
            return Number(generation || 0) === pageGeneration;
        },
    };

    root.dom = {
        addClasses({scope, classes}) {
            const element = document.getElementById(`pywebio-scope-${scope}`);
            if (element) element.classList.add(...classes);
        },
        removeClasses({scope, classes}) {
            const element = document.getElementById(`pywebio-scope-${scope}`);
            if (element) element.classList.remove(...classes);
        },
        setClasses({scope, classes}) {
            const element = document.getElementById(`pywebio-scope-${scope}`);
            if (element) element.className = classes || "";
        },
        scopeExists({scope}) {
            return Boolean(document.getElementById(`pywebio-scope-${scope}`));
        },
        selectorExists({selector}) {
            return Boolean(document.querySelector(selector));
        },
        setControlDisabled({selector, disabled}) {
            const control = document.querySelector(selector);
            if (control) control.disabled = Boolean(disabled);
        },
        removeScope({scope}) {
            document.getElementById(`pywebio-scope-${scope}`)?.remove();
        },
        resetContent() {
            const content = document.getElementById("pywebio-scope-content");
            if (!content) return;
            content.scrollTop = 0;
            content.scrollLeft = 0;
            content.classList.remove("map-content");
        },
        setTheme({theme}) {
            document.body.classList.remove("light-palsitter", "dark-palsitter");
            document.body.classList.add(`${theme}-palsitter`);
        },
    };

    root.storage = {
        get({key}) {
            return localStorage.getItem(key);
        },
        set({key, value}) {
            localStorage.setItem(key, value == null ? "" : String(value));
        },
    };
})();
