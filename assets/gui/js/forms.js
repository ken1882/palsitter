(() => {
    "use strict";

    const root = window.Palsitter = window.Palsitter || {};
    let dirtyForm = null;

    const actionsFor = scopeId => document.getElementById(scopeId.replace(/_panel$/, "_actions"));
    const setBusy = (form, busy) => {
        if (!form) return;
        form.busy = Boolean(busy);
        actionsFor(form.scopeId)?.querySelectorAll("button").forEach(button => {
            button.disabled = form.busy;
        });
    };
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
            const form = {scopeId, dirty: false, busy: false, controller};
            dirtyForm?.controller?.abort();
            dirtyForm = form;
            const markIfControl = event => {
                if (event.target.matches?.("input, textarea, select")) mark(form);
            };
            scope.addEventListener("input", markIfControl, {signal: controller.signal});
            scope.addEventListener("change", markIfControl, {signal: controller.signal});
            actionsFor(scopeId)?.addEventListener("click", event => {
                if (!event.target.closest("button")) return;
                if (form.busy) {
                    event.preventDefault();
                    event.stopImmediatePropagation();
                    return;
                }
                setBusy(form, true);
            }, {signal: controller.signal});
        },
        mark() {
            if (dirtyForm) mark(dirtyForm);
        },
        clear() {
            if (dirtyForm) {
                actionsFor(dirtyForm.scopeId)?.classList.remove("dirty");
                setBusy(dirtyForm, false);
            }
            dirtyForm?.controller?.abort();
            dirtyForm = null;
        },
        clearDirty() {
            if (!dirtyForm) return;
            dirtyForm.dirty = false;
            actionsFor(dirtyForm.scopeId)?.classList.remove("dirty");
            setBusy(dirtyForm, false);
        },
        setBusy({busy}) {
            setBusy(dirtyForm, busy);
        },
        setFieldInvalid({name, invalid}) {
            const element = document.querySelector(`[name="${CSS.escape(name)}"]`);
            element?.classList.toggle("field-invalid", Boolean(invalid));
        },
    };
})();
