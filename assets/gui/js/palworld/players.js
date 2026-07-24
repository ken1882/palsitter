(function (root) {
  "use strict";

  const palworld = root.Palsitter.palworld = root.Palsitter.palworld || {};
  const api = palworld.players = palworld.players || {};
  let compactTimer = null;
  let detailTimer = null;
  let compactController = null;
  let detailController = null;
  let compactStartTimer = null;
  let detailStartTimer = null;

  const clickRefresh = scopeName => {
    const scope = document.getElementById(`pywebio-scope-${scopeName}`);
    const button = scope?.querySelector("button");
    if (!button) return false;
    button.click();
    return true;
  };
  const startTimer = (kind, scopeName, interval) => {
    const timerKey = kind === "compact" ? "compactTimer" : "detailTimer";
    if (timerKey === "compactTimer" && compactTimer) clearInterval(compactTimer);
    if (timerKey === "detailTimer" && detailTimer) clearInterval(detailTimer);
    const refresh = () => {
      if (clickRefresh(scopeName)) return;
      if (timerKey === "compactTimer") { clearInterval(compactTimer); compactTimer = null; }
      else { clearInterval(detailTimer); detailTimer = null; }
    };
    const timer = setInterval(refresh, interval);
    if (timerKey === "compactTimer") compactTimer = timer;
    else detailTimer = timer;
    const startTimerKey = timerKey === "compactTimer" ? "compactStartTimer" : "detailStartTimer";
    if (startTimerKey === "compactStartTimer" && compactStartTimer) clearTimeout(compactStartTimer);
    if (startTimerKey === "detailStartTimer" && detailStartTimer) clearTimeout(detailStartTimer);
    const starter = setTimeout(() => {
      if (startTimerKey === "compactStartTimer") compactStartTimer = null;
      else detailStartTimer = null;
      refresh();
    }, 0);
    if (startTimerKey === "compactStartTimer") compactStartTimer = starter;
    else detailStartTimer = starter;
  };

  const bindPlayerIdControls = (scopeName, controller) => {
    document.getElementById(`pywebio-scope-${scopeName}`)?.addEventListener("click", event => {
      const toggle = event.target.closest("[data-player-id-toggle]");
      if (toggle) {
        const row = toggle.closest("[data-player-id-row]");
        const value = row?.querySelector("[data-player-id-value]");
        if (!value) return;
        const visible = value.dataset.playerIdVisible === "true";
        value.dataset.playerIdVisible = String(!visible);
        value.textContent = visible
          ? value.dataset.playerIdMaskedValue
          : value.dataset.playerIdVisibleValue;
        toggle.setAttribute("aria-label", visible ? toggle.dataset.show : toggle.dataset.hide);
        row.querySelector('[data-player-id-icon="show"]').hidden = !visible;
        row.querySelector('[data-player-id-icon="hide"]').hidden = visible;
        return;
      }
      const copy = event.target.closest("[data-player-id-copy]");
      if (copy) navigator.clipboard.writeText(copy.parentElement.querySelector("[data-userid]").dataset.userid);
    }, { signal: controller.signal });
  };

  api.mountCompact = ({ interval, generation }) => {
    if (generation != null && !root.Palsitter.page.isCurrent(generation)) return;
    compactController?.abort();
    compactController = new AbortController();
    bindPlayerIdControls("players_panel", compactController);
    startTimer("compact", "players_auto_refresh", interval || 3000);
  };
  api.destroyCompact = function () {
    if (compactTimer) clearInterval(compactTimer);
    if (compactStartTimer) clearTimeout(compactStartTimer);
    compactTimer = null;
    compactStartTimer = null;
    compactController?.abort();
    compactController = null;
  };
  api.mountDetail = function ({ interval, generation }) {
    if (generation != null && !root.Palsitter.page.isCurrent(generation)) return;
    startTimer("detail", "players_detail_auto_refresh", interval || 1000);
    detailController?.abort();
    detailController = new AbortController();
    bindPlayerIdControls("players_detail_panel", detailController);
  };
  api.destroyDetail = function () {
    if (detailTimer) clearInterval(detailTimer);
    if (detailStartTimer) clearTimeout(detailStartTimer);
    detailTimer = null;
    detailStartTimer = null;
    detailController?.abort();
    detailController = null;
  };
  api.updateTitle = function ({ value }) {
    const title = document.querySelector("[data-players-title]");
    if (title && title.textContent !== value) title.textContent = value;
  };
  api.removeRows = function ({ scopes }) {
    for (const scope of scopes || []) document.getElementById(`pywebio-scope-${scope}`)?.remove();
  };
  api.removeEmpty = function ({ scope }) {
    document.getElementById(`pywebio-scope-${scope}`)?.remove();
  };
  api.updateRow = function ({ scope, values }) {
    const row = document.getElementById(`pywebio-scope-${scope}`);
    if (!row) return;
    for (const [field, value] of Object.entries(values || {})) {
      const target = row.querySelector(`[data-player-field="${field}"]`);
      if (target && target.textContent !== value) target.textContent = value;
    }
  };
  api.orderRows = function ({ containerScope, scopes }) {
    const container = document.getElementById(`pywebio-scope-${containerScope}`);
    if (!container) return;
    for (const scope of scopes || []) {
      const row = document.getElementById(`pywebio-scope-${scope}`);
      if (row) container.appendChild(row);
    }
  };
  api.destroy = function () {
    api.destroyCompact();
    api.destroyDetail();
  };
})(window);
