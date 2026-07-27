(() => {
    "use strict";

    const root = window.Palsitter = window.Palsitter || {};
    let dirtyForm = null;
    let activeHelp = null;
    let helpTooltip = null;
    let positionHelp = null;

    const hideHelp = () => {
        helpTooltip?.remove();
        activeHelp = null;
        helpTooltip = null;
        positionHelp = null;
    };

    const showHelp = icon => {
        hideHelp();
        const text = icon.getAttribute("data-tooltip") || "";
        if (!text) return;
        const tooltip = document.createElement("span");
        tooltip.className = "field-help-tooltip";
        tooltip.setAttribute("role", "tooltip");
        tooltip.textContent = text;
        document.body.appendChild(tooltip);
        activeHelp = icon;
        helpTooltip = tooltip;
        positionHelp = () => {
            if (!activeHelp?.isConnected || !helpTooltip?.isConnected) {
                hideHelp();
                return;
            }
            const iconRect = activeHelp.getBoundingClientRect();
            const tooltipRect = helpTooltip.getBoundingClientRect();
            const margin = 8;
            const gap = 8;
            const viewportWidth = document.documentElement.clientWidth || window.innerWidth;
            const viewportHeight = document.documentElement.clientHeight || window.innerHeight;
            let left = iconRect.right + gap;
            if (left + tooltipRect.width > viewportWidth - margin) {
                left = iconRect.left - gap - tooltipRect.width;
            }
            left = Math.max(margin, Math.min(left, viewportWidth - margin - tooltipRect.width));
            let top = iconRect.top + (iconRect.height - tooltipRect.height) / 2;
            top = Math.max(margin, Math.min(top, viewportHeight - margin - tooltipRect.height));
            helpTooltip.style.left = `${Math.round(left)}px`;
            helpTooltip.style.top = `${Math.round(top)}px`;
        };
        positionHelp();
    };

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
            hideHelp();
            const controller = new AbortController();
            const form = {scopeId, dirty: false, busy: false, controller};
            dirtyForm?.controller?.abort();
            dirtyForm = form;
            const markIfControl = event => {
                if (event.target.matches?.("input, textarea, select")) mark(form);
            };
            scope.addEventListener("input", markIfControl, {signal: controller.signal});
            scope.addEventListener("change", markIfControl, {signal: controller.signal});
            scope.addEventListener("pointerover", event => {
                const icon = event.target.closest?.(".field-help");
                if (icon && (!event.relatedTarget || !icon.contains(event.relatedTarget))) {
                    showHelp(icon);
                }
            }, {signal: controller.signal});
            scope.addEventListener("pointerout", event => {
                const icon = event.target.closest?.(".field-help");
                if (icon && !icon.contains(event.relatedTarget) && activeHelp === icon) hideHelp();
            }, {signal: controller.signal});
            scope.addEventListener("focusin", event => {
                const icon = event.target.closest?.(".field-help");
                if (icon) showHelp(icon);
            }, {signal: controller.signal});
            scope.addEventListener("focusout", event => {
                const icon = event.target.closest?.(".field-help");
                if (icon && activeHelp === icon && !icon.contains(event.relatedTarget)) hideHelp();
            }, {signal: controller.signal});
            window.addEventListener("resize", () => positionHelp?.(), {signal: controller.signal});
            window.addEventListener("scroll", () => positionHelp?.(), {
                signal: controller.signal,
                passive: true,
            });
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
            hideHelp();
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
