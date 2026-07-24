(() => {
    "use strict";
    const root = window.Palsitter = window.Palsitter || {};
    root.utils = {
        selectedInstances() {
            return Array.from(document.querySelectorAll(".utils-instance-checkbox:checked"))
                .map(element => element.dataset.instanceName);
        },
        updateLog({content, keepBottom}) {
            const scope = document.getElementById("pywebio-scope-dev-log");
            const output = document.getElementById("utils-log-output");
            if (!scope || !output) return;
            const current = output.textContent;
            if (content !== current) {
                if (current && content.startsWith(`${current}\n`)) output.appendChild(document.createTextNode(content.slice(current.length)));
                else output.textContent = content;
            }
            if (keepBottom) scope.scrollTop = scope.scrollHeight;
        },
    };
    let shutdownTimer = null;
    const mountShutdown = ({forceAt, selector, enabled}) => {
        if (shutdownTimer !== null) clearInterval(shutdownTimer);
        const update = () => {
            const button = document.querySelector(selector);
            if (!button) return;
            const remaining = Math.max(0, Math.ceil(Number(forceAt) - Date.now() / 1000));
            button.textContent = remaining > 0
                ? `${t("utils.force_shutdown")} (${remaining})`
                : t("utils.force_shutdown");
            button.disabled = !enabled || remaining > 0;
            if (remaining === 0) {
                clearInterval(shutdownTimer);
                shutdownTimer = null;
            }
        };
        update();
        shutdownTimer = setInterval(update, 250);
    };
    root.shutdown = {
        mount: mountShutdown,
        update: mountShutdown,
        destroy() {
            if (shutdownTimer !== null) clearInterval(shutdownTimer);
            shutdownTimer = null;
        },
    };
})();
