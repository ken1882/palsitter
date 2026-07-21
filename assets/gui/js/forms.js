(() => {
    "use strict";

    const root = window.Palsitter = window.Palsitter || {};
    let dirtyForm = null;

    const actionsFor = scopeId => document.getElementById(scopeId.replace(/_panel$/, "_actions"));
    const mark = form => {
        if (dirtyForm !== form) return;
        form.dirty = true;
        actionsFor(form.scopeId)?.classList.add("dirty");
    };

    root.forms = {
        isDirty() {
            return Boolean(dirtyForm?.scopeId && dirtyForm.dirty);
        },
        register({scopeId}) {
            const scope = document.getElementById(scopeId);
            if (!scope) return;
            const controller = new AbortController();
            const form = {scopeId, dirty: false, controller};
            dirtyForm?.controller?.abort();
            dirtyForm = form;
            for (const element of scope.querySelectorAll("input, textarea, select")) {
                element.addEventListener("input", () => mark(form), {signal: controller.signal});
                element.addEventListener("change", () => mark(form), {signal: controller.signal});
            }
        },
        mark() {
            if (dirtyForm) mark(dirtyForm);
        },
        clear() {
            dirtyForm?.controller?.abort();
            dirtyForm = null;
        },
        setFieldInvalid({name, invalid}) {
            const element = document.querySelector(`[name="${CSS.escape(name)}"]`);
            element?.classList.toggle("field-invalid", Boolean(invalid));
        },
    };
})();
