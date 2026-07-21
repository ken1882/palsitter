(() => {
    "use strict";

    const root = window.Palsitter = window.Palsitter || {};
    root.instance = {
        configureDeleteConfirmation({target}) {
            const scope = document.getElementById("pywebio-scope-delete_confirm");
            const input = document.querySelector('input[name="delete_confirm_name"]');
            const button = scope?.querySelector("button");
            if (!input || !button) return;
            const sync = () => { button.disabled = input.value.trim() !== target; };
            input.addEventListener("input", sync, {once: false});
            sync();
        },
    };
})();
