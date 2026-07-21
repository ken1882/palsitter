(() => {
    "use strict";
    const root = window.Palsitter = window.Palsitter || {};
    const field = (card, name) => card.querySelector(`[data-home-field="${name}"]`);
    root.home = {
        isRendered({scope}) {
            return Boolean(document.querySelector(`#pywebio-scope-${CSS.escape(scope)} > .instance-card:not(.instance-card-loading)`));
        },
        updateCard({scope, data}) {
            const card = document.querySelector(`#pywebio-scope-${CSS.escape(scope)} > .instance-card`);
            if (!card) return;
            card.className = `instance-card instance-card-${data.state}`;
            for (const name of ["name", "state_label", "summary", "players", "fps", "cpu", "memory", "endpoints", "version", "backup", "unsupported_message"]) {
                const target = field(card, name);
                if (target && data[name] !== undefined && target.textContent !== String(data[name])) target.textContent = String(data[name]);
            }
            const progressRoot = card.querySelector("[data-home-progress]");
            if (!progressRoot) return;
            if (!data.progress) {
                progressRoot.replaceChildren();
                return;
            }
            let progress = progressRoot.querySelector(".instance-progress");
            if (!progress) {
                progress = document.createElement("div");
                progress.className = "instance-progress";
                progress.append(document.createElement("strong"), document.createTextNode(" "));
                progressRoot.appendChild(progress);
            }
            progress.dataset.operationKind = data.progress.kind || "";
            progress.querySelector("strong").textContent = `${data.progress.phase || ""}${data.progress.percent || ""}`;
            progress.lastChild.data = ` ${data.progress.message || ""}`;
        },
    };
})();
