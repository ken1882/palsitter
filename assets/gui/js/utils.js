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
})();
