(function (root) {
  "use strict";

  const palworld = root.Palsitter.palworld = root.Palsitter.palworld || {};
  const api = palworld.players = palworld.players || {};
  let compactTimer = null;
  let detailTimer = null;
  let detailController = null;

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
    setTimeout(refresh, 0);
  };

  api.mountCompact = ({ interval }) => startTimer("compact", "players_auto_refresh", interval || 3000);
  api.destroyCompact = function () {
    if (compactTimer) clearInterval(compactTimer);
    compactTimer = null;
  };
  api.mountDetail = function ({ interval }) {
    startTimer("detail", "players_detail_auto_refresh", interval || 1000);
    detailController?.abort();
    detailController = new AbortController();
    document.getElementById("pywebio-scope-players_detail_panel")?.addEventListener("click", event => {
      const reveal = event.target.closest("[data-player-id-reveal]");
      if (reveal) {
        const value = reveal.parentElement.querySelector("[data-userid]");
        const hidden = value.textContent === "••••";
        value.textContent = hidden ? value.dataset.userid : "••••";
        reveal.textContent = hidden ? reveal.dataset.hide : reveal.dataset.show;
        return;
      }
      const copy = event.target.closest("[data-player-id-copy]");
      if (copy) navigator.clipboard.writeText(copy.parentElement.querySelector("[data-userid]").dataset.userid);
    }, { signal: detailController.signal });
  };
  api.destroyDetail = function () {
    if (detailTimer) clearInterval(detailTimer);
    detailTimer = null;
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
