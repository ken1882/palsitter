(() => {
    "use strict";

    const root = window.Palsitter = window.Palsitter || {};
    const layouts = new Map();

    const destroy = layoutScope => {
        const state = layouts.get(layoutScope);
        if (!state) return;
        state.controller.abort();
        state.observer.disconnect();
        state.content?.classList.remove("section-layout-content");
        layouts.delete(layoutScope);
    };

    const sync = layoutScope => {
        const state = layouts.get(layoutScope);
        if (!state) return;
        for (const button of state.navigator.querySelectorAll("[data-section-target]")) {
            const target = document.getElementById(`pywebio-scope-${button.dataset.sectionTarget}`);
            button.hidden = !target || target.hidden;
        }
    };

    root.sectionLayout = {
        mount({layoutScope, groupsScope, sectionScopes, generation}) {
            if (generation != null && !root.page.isCurrent(generation)) return;
            destroy(layoutScope);
            const layout = document.getElementById(`pywebio-scope-${layoutScope}`);
            const groups = document.getElementById(`pywebio-scope-${groupsScope}`);
            const navigator = layout?.querySelector(".section-layout-navigator");
            if (!layout || !groups || !navigator) return;
            const controller = new AbortController();
            const content = layout.closest("#pywebio-scope-content");
            content?.classList.add("section-layout-content");
            navigator.addEventListener("click", event => {
                const button = event.target.closest("[data-section-target]");
                if (!button || button.hidden) return;
                const target = document.getElementById(
                    `pywebio-scope-${button.dataset.sectionTarget}`
                );
                if (!target || target.hidden) return;
                groups.scrollTo({
                    top: Math.max(0, target.offsetTop),
                    behavior: "auto",
                });
            }, {signal: controller.signal});
            const observer = new MutationObserver(() => sync(layoutScope));
            for (const scope of sectionScopes || []) {
                const target = document.getElementById(`pywebio-scope-${scope}`);
                if (target) observer.observe(target, {attributes: true, attributeFilter: ["hidden"]});
            }
            layouts.set(layoutScope, {controller, observer, content, groups, navigator});
            sync(layoutScope);
        },
        sync({layoutScope}) {
            sync(layoutScope);
        },
        destroy({layoutScope}) {
            destroy(layoutScope);
        },
    };
})();
