(function (root) {
  "use strict";

  const palworld = root.Palsitter.palworld = root.Palsitter.palworld || {};
  const api = palworld.overview = palworld.overview || {};
  let rows = [];
  let rowNodes = new Map();
  let nextRowId = 0;
  let emptyText = "";
  let types = [];
  let selectedTypes = new Set();
  let consoleController = null;
  let filterController = null;
  let consoleMountTimer = null;
  const consoleTimers = new Set();

  const later = (callback, delay) => {
    const timer = setTimeout(() => {
      consoleTimers.delete(timer);
      callback();
    }, delay);
    consoleTimers.add(timer);
    return timer;
  };

  const createRows = items => items.map(item => ({
    id: `overview-log-${++nextRowId}`,
    text: item.text,
    type: item.type,
  }));

  const renderLog = (keepBottom = false) => {
    const log = document.querySelector("#overview-log-box");
    if (!log) return;
    if (!rows.length) {
      log.replaceChildren();
      log.dataset.empty = "true";
      log.textContent = emptyText;
      if (keepBottom) log.scrollTop = log.scrollHeight;
      return;
    }
    if (log.dataset.empty === "true") log.replaceChildren();
    log.dataset.empty = "false";
    const visibleRows = rows.filter(item => selectedTypes.has(item.type));
    let cursor = log.firstElementChild;
    for (const item of visibleRows) {
      let row = rowNodes.get(item.id);
      if (!row) {
        row = document.createElement("span");
        row.className = "overview-log-line";
        rowNodes.set(item.id, row);
      }
      row.dataset.logId = item.id;
      row.dataset.logType = item.type;
      row.textContent = item.text;
      if (row !== cursor) log.insertBefore(row, cursor);
      cursor = row.nextElementSibling;
    }
    while (cursor) {
      const next = cursor.nextElementSibling;
      cursor.remove();
      cursor = next;
    }
    if (keepBottom) log.scrollTop = log.scrollHeight;
  };

  api.mountLog = function ({ types: configuredTypes, emptyText: configuredEmpty, generation }) {
    if (generation != null && !root.Palsitter.page.isCurrent(generation)) return;
    rows = [];
    rowNodes = new Map();
    nextRowId = 0;
    emptyText = configuredEmpty;
    types = configuredTypes || [];
    selectedTypes = new Set(types);
    const log = document.querySelector("#overview-log-box");
    if (log) {
      log.replaceChildren();
      log.dataset.empty = "true";
    }
  };

  api.setLog = function ({ items, keepBottom, emptyText: configuredEmpty }) {
    rows = createRows(items || []);
    rowNodes = new Map();
    emptyText = configuredEmpty;
    renderLog(keepBottom);
  };

  api.appendLog = function ({ items, droppedLines, keepBottom }) {
    const log = document.querySelector("#overview-log-box");
    if (!log) return;
    let remaining = droppedLines || 0;
    while (remaining > 0 && rows.length) {
      const dropped = rows.shift();
      rowNodes.get(dropped.id)?.remove();
      rowNodes.delete(dropped.id);
      remaining -= 1;
    }
    const appended = createRows(items || []);
    rows.push(...appended);
    if (!rows.length) return renderLog(keepBottom);
    if (log.dataset.empty === "true") log.replaceChildren();
    log.dataset.empty = "false";
    for (const item of appended) {
      if (!selectedTypes.has(item.type)) continue;
      const row = document.createElement("span");
      row.className = "overview-log-line";
      row.dataset.logId = item.id;
      row.dataset.logType = item.type;
      row.textContent = item.text;
      rowNodes.set(item.id, row);
      log.appendChild(row);
    }
    if (keepBottom) log.scrollTop = log.scrollHeight;
  };

  api.mountFilter = function ({ generation } = {}) {
    if (generation != null && !root.Palsitter.page.isCurrent(generation)) return;
    filterController?.abort();
    filterController = new AbortController();
    const signal = filterController.signal;
    for (const checkbox of document.querySelectorAll(".overview-log-filter-checkbox")) {
      checkbox.checked = selectedTypes.has(checkbox.dataset.logType);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) selectedTypes.add(checkbox.dataset.logType);
        else selectedTypes.delete(checkbox.dataset.logType);
        renderLog();
      }, { signal });
    }
  };

  api.mountConsole = function ({ generation } = {}) {
    if (generation != null && !root.Palsitter.page.isCurrent(generation)) return;
    if (consoleMountTimer) clearTimeout(consoleMountTimer);
    consoleMountTimer = later(() => {
      consoleMountTimer = null;
      const input = document.querySelector('input[name="console_command"]');
      const list = document.getElementById("console-autocomplete");
      if (!input || !list) return;
      consoleController?.abort();
      consoleController = new AbortController();
      const signal = consoleController.signal;
      input.setAttribute("role", "combobox");
      input.setAttribute("aria-autocomplete", "list");
      input.setAttribute("aria-controls", list.id);
      input.setAttribute("aria-expanded", "false");
      input.setAttribute("autocomplete", "off");
      const options = Array.from(list.querySelectorAll('[role="option"]'));
      let activeOption = null;
      const setOpen = open => { list.hidden = !open; input.setAttribute("aria-expanded", String(open)); };
      const setActive = option => {
        for (const candidate of options) {
          const selected = candidate === option;
          candidate.classList.toggle("console-autocomplete-active", selected);
          candidate.setAttribute("aria-selected", String(selected));
        }
        activeOption = option;
        if (option) {
          input.setAttribute("aria-activedescendant", option.id);
          option.scrollIntoView({ block: "nearest" });
        } else input.removeAttribute("aria-activedescendant");
      };
      const visibleOptions = () => options.filter(option => !option.hidden);
      const showMatches = () => {
        const query = input.value.trim().toLowerCase();
        let visible = 0;
        for (const option of options) {
          const matches = !query || option.textContent.toLowerCase().includes(query);
          option.hidden = !matches;
          if (matches) visible += 1;
        }
        setActive(null);
        setOpen(visible > 0);
      };
      const choose = option => {
        input.value = (option.dataset.command || "") + (option.textContent.includes("<") ? " " : "");
        input.dispatchEvent(new Event("input", { bubbles: true }));
        setActive(null);
        setOpen(false);
        input.focus();
      };
      input.addEventListener("focus", showMatches, { signal });
      input.addEventListener("input", showMatches, { signal });
      input.addEventListener("blur", () => later(() => { setActive(null); setOpen(false); }, 100), { signal });
      input.addEventListener("keydown", event => {
        if (event.key === "Escape") {
          event.preventDefault(); setActive(null); setOpen(false); return;
        }
        if (event.key === "ArrowDown" || event.key === "ArrowUp" || event.key === "Tab") {
          const matches = visibleOptions();
          if (!matches.length) return;
          event.preventDefault();
          const backwards = event.key === "ArrowUp" || (event.key === "Tab" && event.shiftKey);
          const current = matches.indexOf(activeOption);
          const next = current === -1 ? (backwards ? matches.length - 1 : 0)
            : (current + (backwards ? -1 : 1) + matches.length) % matches.length;
          setOpen(true); setActive(matches[next]); return;
        }
        if (event.key !== "Enter") return;
        event.preventDefault();
        if (!list.hidden && activeOption && !activeOption.hidden) return choose(activeOption);
        setOpen(false);
        document.querySelector("#pywebio-scope-console_bar button:not(.console-autocomplete-option)")?.click();
      }, { signal });
      for (const option of options) {
        option.addEventListener("mousedown", event => { event.preventDefault(); choose(option); }, { signal });
      }
    }, 0);
  };

  api.clearConsole = function () {
    const input = document.querySelector('input[name="console_command"]');
    const list = document.getElementById("console-autocomplete");
    if (input) {
      input.value = "";
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
    }
    if (list) {
      list.hidden = true;
      for (const option of list.querySelectorAll('[role="option"]')) {
        option.classList.remove("console-autocomplete-active");
        option.setAttribute("aria-selected", "false");
      }
    }
  };

  api.scrollBottom = function () {
    const log = document.querySelector("#overview-log-box");
    if (log) log.scrollTop = log.scrollHeight;
  };

  api.updateMetrics = function ({ values, updateAvailable, updateTooltip }) {
    for (const [key, value] of Object.entries(values || {})) {
      const target = document.querySelector(`[data-metric="${key}"] .metric-value`);
      if (!target) continue;
      const showUpdate = key === "game-version" && updateAvailable;
      const marker = target.querySelector(".metric-update-available");
      if (target.firstChild?.nodeValue !== value) {
        target.firstChild?.remove();
        target.prepend(document.createTextNode(value));
      }
      if (showUpdate && !marker) {
        const updateMarker = document.createElement("b");
        updateMarker.className = "metric-update-available";
        updateMarker.setAttribute("data-tooltip", updateTooltip || "");
        updateMarker.setAttribute("tabindex", "0");
        updateMarker.textContent = "(↑)";
        target.append(" ", updateMarker);
      } else if (showUpdate && marker) {
        marker.setAttribute("data-tooltip", updateTooltip || "");
      } else if (!showUpdate && marker) {
        marker.previousSibling?.remove();
        marker.remove();
      }
    }
  };

  api.logContains = function ({ text }) {
    return rows.some(row => row.text.includes(text));
  };

  api.destroy = function () {
    if (consoleMountTimer) clearTimeout(consoleMountTimer);
    consoleMountTimer = null;
    for (const timer of consoleTimers) clearTimeout(timer);
    consoleTimers.clear();
    consoleController?.abort();
    filterController?.abort();
    consoleController = null;
    filterController = null;
    rows = [];
    rowNodes.clear();
  };
})(window);
