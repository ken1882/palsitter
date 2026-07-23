(() => {
    "use strict";

    const root = window.Palsitter = window.Palsitter || {};
    const groups = new Map();

    const findGroup = scope => [...document.querySelectorAll("[data-checkbox-group]")]
        .find(group => group.dataset.checkboxGroup === scope);

    const destroy = scope => {
        const instance = groups.get(scope);
        if (!instance) return;
        instance.controller.abort();
        groups.delete(scope);
    };

    const mount = ({scope}) => {
        destroy(scope);
        const group = findGroup(scope);
        if (!group) return;

        const controller = new AbortController();
        const signal = controller.signal;
        const button = group.querySelector(".checkbox-select-all-button");
        const checkboxes = () => [...group.querySelectorAll('input[type="checkbox"]')];
        const update = () => {
            const inputs = checkboxes();
            const allSelected = inputs.length > 0 && inputs.every(input => input.checked);
            if (!button) return;
            button.textContent = allSelected
                ? button.dataset.selectNoneLabel
                : button.dataset.selectAllLabel;
            button.disabled = inputs.length === 0;
            button.setAttribute("aria-pressed", String(allSelected));
        };

        for (const input of checkboxes()) {
            input.addEventListener("change", update, {signal});
        }
        button?.addEventListener("click", () => {
            const inputs = checkboxes();
            const allSelected = inputs.length > 0 && inputs.every(input => input.checked);
            for (const input of inputs) {
                input.checked = !allSelected;
                input.dispatchEvent(new Event("change", {bubbles: true}));
            }
            update();
        }, {signal});
        groups.set(scope, {controller});
        update();
    };

    root.checkboxGroups = {
        mount,
        destroy({scope}) {
            destroy(scope);
        },
    };
})();
