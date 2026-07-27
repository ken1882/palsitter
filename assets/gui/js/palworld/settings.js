(function (root) {
  "use strict";

  const palsitter = root.Palsitter;
  const palworld = palsitter.palworld = palsitter.palworld || {};
  const replaceController = owner => {
    owner.controller?.abort();
    owner.controller = new AbortController();
    return owner.controller.signal;
  };
  const later = (owner, callback) => {
    owner.timers = owner.timers || new Set();
    const timer = setTimeout(() => {
      owner.timers.delete(timer);
      callback();
    }, 0);
    owner.timers.add(timer);
    return timer;
  };
  const clearTimers = owner => {
    for (const timer of owner.timers || []) clearTimeout(timer);
    owner.timers?.clear();
  };

  const server = palworld.serverSettings = palworld.serverSettings || {};
  server.mount = function ({generation, sectionScopes, layoutScope} = {}) {
    if (generation != null && !palsitter.page.isCurrent(generation)) return;
    const form = document.getElementById("pywebio-scope-settings_form");
    const toolbar = document.getElementById("pywebio-scope-settings_filter_toolbar");
    if (!form || !toolbar) return;
    const signal = replaceController(server);
    const sections = (sectionScopes || [])
      .map(scope => document.getElementById(`pywebio-scope-${scope}`))
      .filter(Boolean);
    for (const section of sections) {
      let priorSearch = "";
      for (const child of section.children) {
        if (child.classList.contains("settings-category-heading")) {
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
        child.dataset.settingsSearch = priorSearch.toLocaleLowerCase();
      }
    }
    const state = { query: "" };
    const apply = () => {
      for (const item of form.querySelectorAll(".server-settings-filter-item")) {
        const searchMatch = !state.query || (item.dataset.settingsSearch || "").includes(state.query);
        item.hidden = !searchMatch;
      }
      for (const section of sections) {
        const hasVisible = Array.from(section.querySelectorAll(".server-settings-filter-item"))
          .some(item => !item.hidden);
        section.hidden = !hasVisible;
        section.querySelectorAll("[data-settings-heading]").forEach(heading => {
          heading.hidden = !hasVisible;
        });
      }
      palsitter.sectionLayout.sync({layoutScope});
    };
    toolbar.querySelector("#server-settings-search")?.addEventListener("input", event => {
      state.query = String(event.target.value || "").trim().toLocaleLowerCase();
      apply();
    }, { signal });
    const updateControlledArgument = (prefix, value, disabled = false) => {
      const nextValue = String(value || "").trim();
      form.querySelectorAll('input[name^="settings_extra_args_controlled_"]').forEach(control => {
        if (!control.value.startsWith(prefix)) return;
        control.value = `${prefix}${nextValue}`;
        const row = control.closest(".argument-controlled-input")?.parentElement;
        if (row) row.hidden = disabled;
      });
    };
    form.addEventListener("input", event => {
      if (event.target.name === "settings_launch_worker_threads_server") {
        updateControlledArgument("-NumberOfWorkerThreadsServer=", event.target.value);
      } else if (event.target.name === "settings_query_port") {
        const value = String(event.target.value || "").trim();
        updateControlledArgument("-queryport=", value, value === "0");
      }
    }, { signal });
    const queryPort = form.querySelector('input[name="settings_query_port"]');
    if (queryPort) {
      const value = String(queryPort.value || "").trim();
      updateControlledArgument("-queryport=", value, value === "0");
    }
    apply();
  };
  server.destroy = () => {
    server.controller?.abort();
    clearTimers(server);
  };

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
  world.mount = function ({
    changedPrefix,
    generation,
    layoutScope,
    sectionScopes,
    toggleValues,
  }) {
    if (generation != null && !palsitter.page.isCurrent(generation)) return;
    const form = document.getElementById("pywebio-scope-world_settings_form");
    const toolbar = document.getElementById("pywebio-scope-world_settings_toolbar");
    if (!form || !toolbar) return;
    const signal = replaceController(world);
    const state = { query: "", changedOnly: false };
    const sections = (sectionScopes || [])
      .map(scope => document.getElementById(`pywebio-scope-${scope}`))
      .filter(Boolean);
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
        const visible = (!query || (scope.dataset.worldSearch || "").includes(query))
          && (!state.changedOnly || scope.dataset.changed === "true");
        scope.classList.toggle("world-field-hidden", !visible);
        scope.dataset.visible = String(visible);
        if (visible) visibleCategories.add(scope.dataset.worldCategory);
      });
      form.querySelectorAll("[data-world-heading]").forEach(heading => {
        const category = heading.dataset.worldHeading;
        heading.hidden = !visibleCategories.has(category);
      });
      for (const section of sections) {
        section.hidden = !Array.from(section.querySelectorAll(".world-field-scope"))
          .some(scope => !scope.classList.contains("world-field-hidden"));
      }
      palsitter.sectionLayout.sync({layoutScope});
    };
    const updateCount = () => {
      const target = document.getElementById("world-changed-count");
      if (target) target.textContent = `${changedPrefix}${scopes().filter(scope => scope.dataset.changed === "true").length}`;
    };
    const sync = (scope, toggleValue) => {
      if (!scope) return;
      if (toggleValue !== undefined) {
        scope.dataset.currentToggleValue = String(Boolean(toggleValue));
      }
      if (scope.dataset.initialToggleValue !== undefined) {
        scope.dataset.changed = String(
          scope.dataset.currentToggleValue !== scope.dataset.initialToggleValue
        );
        updateCount();
        apply();
        return;
      }
      const value = currentValue(scope);
      if (scope.dataset.initialValue === undefined) scope.dataset.initialValue = value;
      scope.dataset.changed = String(value !== scope.dataset.initialValue);
      updateCount();
      apply();
    };
    for (const [key, value] of Object.entries(toggleValues || {})) {
      const scope = document.getElementById(`pywebio-scope-world_field_${key}`);
      if (!scope) continue;
      scope.dataset.initialToggleValue = String(Boolean(value));
      scope.dataset.currentToggleValue = String(Boolean(value));
    }
    world.syncByKey = ({ key, value }) =>
      sync(document.getElementById(`pywebio-scope-world_field_${key}`), value);
    world.markSaved = ({ toggleValues: savedToggleValues } = {}) => later(world, () => {
      scopes().forEach(scope => {
        const key = scope.id.replace("pywebio-scope-world_field_", "");
        if (scope.dataset.initialToggleValue !== undefined) {
          if (Object.prototype.hasOwnProperty.call(savedToggleValues || {}, key)) {
            scope.dataset.currentToggleValue = String(Boolean(savedToggleValues[key]));
          }
          scope.dataset.initialToggleValue = scope.dataset.currentToggleValue;
          scope.dataset.changed = "false";
          return;
        }
        const value = currentValue(scope);
        scope.dataset.initialValue = value;
        scope.dataset.changed = "false";
      });
      updateCount();
      apply();
    });
    scopes().forEach(scope => sync(scope));
    form.addEventListener("input", event => sync(event.target.closest(".world-field-scope")), { signal });
    form.addEventListener("change", event => sync(event.target.closest(".world-field-scope")), { signal });
    const search = document.getElementById("world-settings-search");
    search?.addEventListener("input", () => { state.query = search.value; apply(); }, { signal });
    const changed = document.getElementById("world-changed-only");
    changed?.addEventListener("click", () => {
      state.changedOnly = !state.changedOnly;
      changed.classList.toggle("active", state.changedOnly);
      apply();
    }, { signal });
    apply();
  };
  world.mountPassword = function ({ name, showLabel, hideLabel }) {
    later(world, () => {
      const input = document.querySelector(`input[name="${CSS.escape(name)}"]`);
      const button = input?.closest(".settings-field-control")?.querySelector(".password-eye")
        || input?.parentElement?.parentElement?.querySelector(".password-eye");
      if (!input || !button || button.dataset.passwordMounted) return;
      button.dataset.passwordMounted = "true";
      button.addEventListener("click", () => {
        const show = input.type === "password";
        input.type = show ? "text" : "password";
        button.setAttribute("aria-label", show ? hideLabel : showLabel);
        button.querySelector('[data-password-icon="show"]').hidden = show;
        button.querySelector('[data-password-icon="hide"]').hidden = !show;
      });
    });
  };
  world.destroy = function () {
    world.controller?.abort();
    clearTimers(world);
    delete world.syncByKey;
  };
})(window);
