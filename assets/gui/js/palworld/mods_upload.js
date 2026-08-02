(() => {
    "use strict";

    const root = window.Palsitter = window.Palsitter || {};
    root.palworld = root.palworld || {};
    const api = root.palworld.modsUpload = root.palworld.modsUpload || {};
    const states = new Map();

    const control = name => document.querySelector(`input[name="${CSS.escape(String(name))}"]`);

    Object.assign(api, {
        mountFolder({name}) {
            api.destroy({name});
            const key = String(name);
            const state = {paths: [], listener: null, input: null, timer: null};
            const attach = () => {
                const input = control(key);
                if (!input) {
                    state.timer = window.setTimeout(attach, 25);
                    return;
                }
                state.input = input;
                input.setAttribute("webkitdirectory", "");
                input.setAttribute("directory", "");
                state.listener = () => {
                    state.paths = Array.from(input.files || [], file => file.webkitRelativePath || file.name);
                };
                input.addEventListener("change", state.listener);
            };
            states.set(key, state);
            attach();
        },
        relativePaths({name}) {
            return [...(states.get(String(name))?.paths || [])];
        },
        hasSelection({name}) {
            return Boolean(control(name)?.files?.length);
        },
        setBusy({names, busy}) {
            for (const name of names || []) {
                const input = control(name);
                if (input) input.disabled = Boolean(busy);
            }
        },
        reset({names}) {
            for (const name of names || []) {
                const input = control(name);
                if (input) {
                    input.value = "";
                    input.dispatchEvent(new Event("change", {bubbles: true}));
                }
                const state = states.get(String(name));
                if (state) state.paths = [];
            }
        },
        destroy({name}) {
            const key = String(name);
            const state = states.get(key);
            if (state?.timer) window.clearTimeout(state.timer);
            if (state?.listener && state.input) state.input.removeEventListener("change", state.listener);
            states.delete(key);
        },
    });
})();
