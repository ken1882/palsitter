(function (root) {
  "use strict";

  const palsitter = root.Palsitter;
  const palworld = palsitter.palworld = palsitter.palworld || {};
  const replaceController = owner => {
    owner.controller?.abort();
    owner.controller = new AbortController();
    return owner.controller.signal;
  };

  const server = palworld.serverSettings = palworld.serverSettings || {};
  server.mount = function () {
    const form = document.getElementById("pywebio-scope-settings_form");
    const toolbar = document.getElementById("pywebio-scope-settings_filter_toolbar");
    if (!form || !toolbar) return;
    const signal = replaceController(server);
    let category = "";
    let priorSearch = "";
    for (const child of form.children) {
      if (child.classList.contains("settings-category-heading")) {
        category = child.dataset.settingsHeading || "";
        priorSearch = "";
        continue;
      }
      const label = child.querySelector(".settings-field-label");
      const named = child.querySelector('[name^="settings_"]');
      const toggle = child.querySelector('[id*="settings_toggle_"]');
      const raw = named ? named.name.replace(/^settings_/, "")
        : toggle ? toggle.id.replace(/^pywebio-scope-settings_toggle_/, "")
          : child.querySelector(".settings-inline-note") ? "launch arguments official documentation" : "";
      if (label || raw) priorSearch = `${label ? label.textContent : ""} ${raw}`.trim();
      child.classList.add("server-settings-filter-item");
      child.dataset.settingsCategory = category;
      child.dataset.settingsSearch = priorSearch.toLocaleLowerCase();
    }
    const state = { category: "all", query: "" };
    const apply = () => {
      for (const item of form.querySelectorAll(".server-settings-filter-item")) {
        const categoryMatch = state.category === "all" || item.dataset.settingsCategory === state.category;
        const searchMatch = !state.query || (item.dataset.settingsSearch || "").includes(state.query);
        item.hidden = !(categoryMatch && searchMatch);
      }
      for (const heading of form.querySelectorAll("[data-settings-heading]")) {
        const headingCategory = heading.dataset.settingsHeading;
        const categoryMatch = state.category === "all" || headingCategory === state.category;
        const hasVisible = Array.from(form.querySelectorAll(".server-settings-filter-item"))
          .some(item => !item.hidden && item.dataset.settingsCategory === headingCategory);
        heading.hidden = !(categoryMatch && hasVisible);
      }
    };
    toolbar.querySelectorAll("[data-settings-category]").forEach(button => {
      button.addEventListener("click", () => {
        state.category = button.dataset.settingsCategory;
        toolbar.querySelectorAll("[data-settings-category]").forEach(candidate =>
          candidate.classList.toggle("active", candidate === button));
        apply();
      }, { signal });
    });
    toolbar.querySelector("#server-settings-search")?.addEventListener("input", event => {
      state.query = String(event.target.value || "").trim().toLocaleLowerCase();
      apply();
    }, { signal });
    apply();
  };
  server.destroy = () => server.controller?.abort();

  const world = palworld.worldSettings = palworld.worldSettings || {};
  world.decorateField = function ({ scope, category, search }) {
    const element = document.getElementById(`pywebio-scope-${scope}`);
    if (!element) return;
    element.classList.add("world-field-scope");
    element.dataset.worldCategory = category;
    element.dataset.worldSearch = String(search || "").toLocaleLowerCase();
  };
  world.configureNumeric = function ({ floatNames, intNames }) {
    for (const name of floatNames || []) {
      const input = document.querySelector(`input[name="${CSS.escape(name)}"]`);
      if (input) { input.type = "number"; input.step = "0.1"; }
    }
    for (const name of intNames || []) {
      const input = document.querySelector(`input[name="${CSS.escape(name)}"]`);
      if (input) input.step = "1";
    }
  };
  world.mount = function ({ changedPrefix }) {
    const form = document.getElementById("pywebio-scope-world_settings_form");
    const toolbar = document.getElementById("pywebio-scope-world_settings_toolbar");
    if (!form || !toolbar) return;
    const signal = replaceController(world);
    const state = { category: "all", query: "", changedOnly: false };
    const scopes = () => Array.from(form.querySelectorAll(".world-field-scope"));
    const currentValue = scope => {
      const controls = Array.from(scope.querySelectorAll("input, select, textarea"));
      if (!controls.length) return scope.querySelector(".settings-field-control button")?.textContent.trim() || "";
      return JSON.stringify(controls.map(control =>
        control.type === "checkbox" || control.type === "radio"
          ? [control.value, !!control.checked] : control.value));
    };
    const apply = () => {
      const query = state.query.trim().toLocaleLowerCase();
      const visibleCategories = new Set();
      scopes().forEach(scope => {
        const visible = (state.category === "all" || scope.dataset.worldCategory === state.category)
          && (!query || (scope.dataset.worldSearch || "").includes(query))
          && (!state.changedOnly || scope.dataset.changed === "true");
        scope.classList.toggle("world-field-hidden", !visible);
        scope.dataset.visible = String(visible);
        if (visible) visibleCategories.add(scope.dataset.worldCategory);
      });
      form.querySelectorAll("[data-world-heading]").forEach(heading => {
        const category = heading.dataset.worldHeading;
        heading.hidden = !((state.category === "all" || category === state.category) && visibleCategories.has(category));
      });
    };
    const updateCount = () => {
      const target = document.getElementById("world-changed-count");
      if (target) target.textContent = `${changedPrefix}${scopes().filter(scope => scope.dataset.changed === "true").length}`;
    };
    const sync = scope => {
      if (!scope) return;
      const value = currentValue(scope);
      if (scope.dataset.initialValue === undefined) scope.dataset.initialValue = value;
      scope.dataset.changed = String(value !== scope.dataset.initialValue);
      updateCount();
      apply();
    };
    world.syncByKey = ({ key }) => setTimeout(() =>
      sync(document.getElementById(`pywebio-scope-world_field_${key}`)), 0);
    scopes().forEach(sync);
    form.addEventListener("input", event => sync(event.target.closest(".world-field-scope")), { signal });
    form.addEventListener("change", event => sync(event.target.closest(".world-field-scope")), { signal });
    const search = document.getElementById("world-settings-search");
    search?.addEventListener("input", () => { state.query = search.value; apply(); }, { signal });
    toolbar.querySelectorAll("[data-world-category]").forEach(button => {
      button.addEventListener("click", () => {
        state.category = button.dataset.worldCategory;
        toolbar.querySelectorAll("[data-world-category]").forEach(item => item.classList.toggle("active", item === button));
        apply();
      }, { signal });
    });
    const changed = document.getElementById("world-changed-only");
    changed?.addEventListener("click", () => {
      state.changedOnly = !state.changedOnly;
      changed.classList.toggle("active", state.changedOnly);
      apply();
    }, { signal });
    apply();
  };
  world.mountPassword = function ({ name, showLabel, hideLabel }) {
    setTimeout(() => {
      const input = document.querySelector(`input[name="${CSS.escape(name)}"]`);
      const button = input?.closest(".settings-field-control")?.querySelector(".password-eye")
        || input?.parentElement?.parentElement?.querySelector(".password-eye");
      if (!input || !button || button.dataset.passwordMounted) return;
      button.dataset.passwordMounted = "true";
      button.addEventListener("click", () => {
        const show = input.type === "password";
        input.type = show ? "text" : "password";
        button.setAttribute("aria-label", show ? hideLabel : showLabel);
      });
    }, 0);
  };
  world.destroy = function () {
    world.controller?.abort();
    delete world.syncByKey;
  };
})(window);
