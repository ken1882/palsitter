(() => {
    "use strict";

    const rootApi = window.Palsitter = window.Palsitter || {};
    const instances = new Map();

    const destroy = tableId => {
        const instance = instances.get(tableId);
        if (!instance) return;
        instance.controller.abort();
        instance.resizeObserver?.disconnect();
        instances.delete(tableId);
    };

    const mount = options => {
        const {tableId, rows, columns, tags, tagKey, labels} = options;
        destroy(tableId);
        const root = document.getElementById(tableId);
        if (!root) return;

        const controller = new AbortController();
        const signal = controller.signal;
        const dataRows = Array.isArray(rows) ? rows : [];
        const tableColumns = columns || [];
        const tagValues = new Set(tags || []);
        const search = root.querySelector('input[type="search"]');
        const dateInputs = root.querySelectorAll('input[type="datetime-local"]');
        const start = dateInputs[0];
        const end = dateInputs[1];
        const quick = root.querySelector('select[id$="-quick"]');
        const quickButtons = root.querySelectorAll("[data-quick-value]");
        const timeButton = root.querySelector(".pagination-table-time-button");
        const timePopup = root.querySelector(".pagination-table-time-popup");
        const timeLabel = root.querySelector(".pagination-table-time-label");
        const applyButton = root.querySelector(".pagination-table-apply");
        const size = root.querySelector('select[id$="-page-size"]');
        const tbody = root.querySelector("tbody");
        const empty = root.querySelector(".pagination-table-empty");
        const count = root.querySelector(".pagination-table-count");
        const first = root.querySelector(".pagination-table-first");
        const previous = root.querySelector(".pagination-table-previous");
        const next = root.querySelector(".pagination-table-next");
        const last = root.querySelector(".pagination-table-last");
        const pagesControl = root.querySelector(".pagination-table-pages");
        const tagButton = root.querySelector(".pagination-table-tags-button");
        const tagPopup = root.querySelector(".pagination-table-tags-popup");
        const toolbar = root.querySelector(".pagination-table-toolbar");
        const shell = root.querySelector(".pagination-table-shell");
        const footer = root.querySelector(".pagination-table-footer");
        const table = root.querySelector("table");
        const headerCells = [...root.querySelectorAll("thead th")];
        const columnTracks = [...root.querySelectorAll("col.pagination-table-column")];
        let currentPage = 0;
        let userResizedColumns = false;
        const popoverMargin = 8;

        const listen = (target, event, callback, extra = {}) => {
            target?.addEventListener(event, callback, {...extra, signal});
        };
        const positionPopover = (popover, anchor) => {
            if (!popover || popover.hidden || !anchor) return;
            const anchorRect = anchor.getBoundingClientRect();
            const viewportWidth = document.documentElement.clientWidth || window.innerWidth;
            const viewportHeight = document.documentElement.clientHeight || window.innerHeight;
            const popoverRect = popover.getBoundingClientRect();
            const maxLeft = Math.max(popoverMargin, viewportWidth - popoverRect.width - popoverMargin);
            const left = Math.min(Math.max(popoverMargin, anchorRect.left), maxLeft);
            const below = anchorRect.bottom + popoverMargin;
            const above = anchorRect.top - popoverRect.height - popoverMargin;
            const top = below + popoverRect.height <= viewportHeight - popoverMargin
                ? below
                : above >= popoverMargin
                    ? above
                    : Math.max(popoverMargin, viewportHeight - popoverRect.height - popoverMargin);
            popover.style.left = `${left}px`;
            popover.style.top = `${top}px`;
        };
        const repositionPopovers = () => {
            positionPopover(timePopup, timeButton);
            positionPopover(tagPopup, tagButton);
        };
        const fillLastColumn = () => {
            if (userResizedColumns || !table || columnTracks.length < 1) return;
            const lastTrack = columnTracks[columnTracks.length - 1];
            const fixedWidth = headerCells
                .slice(0, -1)
                .reduce((total, cell) => total + cell.getBoundingClientRect().width, 0);
            const remainingWidth = table.getBoundingClientRect().width - fixedWidth;
            if (remainingWidth > 0) lastTrack.style.width = `${remainingWidth}px`;
        };
        const layoutTable = () => {
            if (!toolbar || !shell || !footer) return;
            const viewportBottom = window.innerHeight - 8;
            const footerMargin = parseFloat(getComputedStyle(footer).marginTop) || 0;
            const available = viewportBottom
                - root.getBoundingClientRect().top
                - toolbar.getBoundingClientRect().height
                - footer.getBoundingClientRect().height
                - footerMargin;
            shell.style.height = `${Math.max(160, available)}px`;
        };
        const setupColumnResize = () => {
            const minimumWidth = 96;
            for (const handle of root.querySelectorAll(".pagination-table-resize-handle")) {
                listen(handle, "pointerdown", event => {
                    const index = Number(handle.dataset.columnIndex);
                    const leftColumn = headerCells[index];
                    const leftTrack = columnTracks[index];
                    if (!leftColumn || !leftTrack) return;
                    event.preventDefault();
                    event.stopPropagation();
                    userResizedColumns = true;
                    const startX = event.clientX;
                    const leftStart = leftColumn.getBoundingClientRect().width;
                    root.classList.add("is-resizing");
                    const move = moveEvent => {
                        const leftWidth = Math.max(
                            minimumWidth, leftStart + moveEvent.clientX - startX
                        );
                        leftTrack.style.width = `${leftWidth}px`;
                    };
                    const stop = () => {
                        root.classList.remove("is-resizing");
                        document.removeEventListener("pointermove", move);
                        document.removeEventListener("pointerup", stop);
                        document.removeEventListener("pointercancel", stop);
                    };
                    document.addEventListener("pointermove", move);
                    document.addEventListener("pointerup", stop, {once: true});
                    document.addEventListener("pointercancel", stop, {once: true});
                });
            }
        };

        const resizeObserver = typeof ResizeObserver === "undefined" || !table
            ? null
            : new ResizeObserver(fillLastColumn);
        resizeObserver?.observe(table);
        setupColumnResize();

        const text = value => value == null ? "" : String(value);
        const parseDate = value => value ? new Date(value) : null;
        const localInput = date => {
            const pad = value => String(value).padStart(2, "0");
            return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
        };
        const formatDate = value => {
            const date = parseDate(value);
            return date && !Number.isNaN(date.getTime())
                ? `${date.toLocaleDateString()}\n${date.toLocaleTimeString()}`
                : text(value);
        };
        const initializeColumnWidths = () => {
            if (!table || !columnTracks.length || !headerCells.length) return;
            const tableWidth = table.getBoundingClientRect().width;
            if (!tableWidth) return;
            const canvas = document.createElement("canvas");
            const context = canvas.getContext("2d");
            if (!context) return;
            const minimumWidths = [];
            const preferredWidths = [];
            for (const [index, column] of tableColumns.entries()) {
                const header = headerCells[index];
                const style = getComputedStyle(header);
                context.font = style.font;
                const padding = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight);
                const measure = value => Math.max(
                    ...String(value).split("\n").map(line => context.measureText(line).width)
                );
                const minimum = Math.max(96, measure(column.label) + padding);
                const contentWidth = Math.max(
                    minimum,
                    ...dataRows.map(row => measure(
                        column.type === "datetime" ? formatDate(row[column.key]) : text(row[column.key])
                    ) + padding)
                );
                minimumWidths.push(minimum);
                preferredWidths.push(Math.min(contentWidth, Math.max(160, tableWidth * .55)));
            }
            const minimumTotal = minimumWidths.reduce((total, width) => total + width, 0);
            const preferredTotal = preferredWidths.reduce((total, width) => total + width, 0);
            const flexibleTotal = preferredTotal - minimumTotal;
            const flexibleSpace = Math.max(0, tableWidth - minimumTotal);
            const scale = flexibleTotal > 0
                ? Math.min(1, flexibleSpace / flexibleTotal)
                : 1;
            preferredWidths.forEach((width, index) => {
                const initialWidth = minimumWidths[index]
                    + (width - minimumWidths[index]) * scale;
                columnTracks[index].style.width = `${initialWidth}px`;
            });
        };
        const selectedTags = () => new Set(
            [...root.querySelectorAll(".pagination-table-tag-option input:checked")]
                .map(input => input.dataset.tagValue)
        );
        const filteredRows = () => {
            const query = text(search?.value).trim().toLocaleLowerCase();
            const selected = selectedTags();
            const startDate = start?.value ? new Date(start.value) : null;
            const endDate = end?.value ? new Date(end.value) : null;
            return dataRows.filter(row => {
                const timestamp = parseDate(row.timestamp);
                if (!timestamp || Number.isNaN(timestamp.getTime())) return false;
                if (startDate && timestamp < startDate) return false;
                if (endDate && timestamp > endDate) return false;
                if (tagValues.size && !selected.has(text(row[tagKey]))) return false;
                if (!query) return true;
                return tableColumns.filter(column => column.searchable).some(column => {
                    const value = column.type === "datetime" ? formatDate(row[column.key]) : text(row[column.key]);
                    return value.toLocaleLowerCase().includes(query);
                });
            });
        };
        const renderPages = pages => {
            pagesControl.replaceChildren();
            const visiblePages = [...new Set([
                0, 1, 2, currentPage, pages - 3, pages - 2, pages - 1,
            ].filter(page => page >= 0 && page < pages))].sort((a, b) => a - b);
            let previousPage = -1;
            for (const page of visiblePages) {
                if (page - previousPage > 1) {
                    const ellipsis = document.createElement("span");
                    ellipsis.className = "pagination-table-ellipsis";
                    ellipsis.textContent = "...";
                    ellipsis.setAttribute("aria-hidden", "true");
                    pagesControl.appendChild(ellipsis);
                }
                if (page === currentPage) {
                    const input = document.createElement("input");
                    input.className = "pagination-table-page-number";
                    input.type = "number";
                    input.value = String(page + 1);
                    input.min = "1";
                    input.max = String(pages);
                    input.setAttribute("aria-label", labels.page_number);
                    pagesControl.appendChild(input);
                } else {
                    const button = document.createElement("button");
                    button.type = "button";
                    button.textContent = String(page + 1);
                    button.dataset.page = String(page);
                    button.setAttribute(
                        "aria-label",
                        labels.page.replace("{page}", String(page + 1)).replace("{pages}", String(pages))
                    );
                    pagesControl.appendChild(button);
                }
                previousPage = page;
            }
        };
        const render = () => {
            const visible = filteredRows();
            const pageSize = Number(size?.value) || 25;
            const pages = Math.max(1, Math.ceil(visible.length / pageSize));
            currentPage = Math.min(currentPage, pages - 1);
            const pageRows = visible.slice(currentPage * pageSize, (currentPage + 1) * pageSize);
            tbody.replaceChildren();
            for (const row of pageRows) {
                const tr = document.createElement("tr");
                for (const column of tableColumns) {
                    const td = document.createElement("td");
                    td.textContent = column.type === "datetime" ? formatDate(row[column.key]) : text(row[column.key]);
                    tr.appendChild(td);
                }
                tbody.appendChild(tr);
            }
            empty.hidden = visible.length !== 0;
            empty.textContent = labels.empty;
            count.textContent = labels.showing.replace("{shown}", String(pageRows.length)).replace("{total}", String(visible.length));
            renderPages(pages);
            first.disabled = currentPage === 0;
            previous.disabled = currentPage === 0;
            next.disabled = currentPage >= pages - 1;
            last.disabled = currentPage >= pages - 1;
        };
        const reset = () => { currentPage = 0; render(); };

        listen(search, "input", reset);
        listen(start, "change", reset);
        listen(end, "change", reset);
        listen(size, "change", reset);
        for (const input of root.querySelectorAll(".pagination-table-tag-option input")) listen(input, "change", reset);
        listen(first, "click", () => { currentPage = 0; render(); });
        listen(previous, "click", () => { currentPage = Math.max(0, currentPage - 1); render(); });
        listen(next, "click", () => { currentPage += 1; render(); });
        listen(last, "click", () => {
            currentPage = Math.max(0, Math.ceil(filteredRows().length / (Number(size?.value) || 25)) - 1);
            render();
        });
        listen(pagesControl, "click", event => {
            const button = event.target.closest("button[data-page]");
            if (button) {
                currentPage = Number(button.dataset.page);
                render();
            }
        });
        listen(pagesControl, "change", event => {
            if (!event.target.matches(".pagination-table-page-number")) return;
            const page = Number(event.target.value);
            const pages = Math.max(1, Math.ceil(filteredRows().length / (Number(size?.value) || 25)));
            currentPage = Number.isFinite(page) ? Math.min(Math.max(1, page), pages) - 1 : currentPage;
            render();
        });
        listen(quick, "change", () => {
            const value = quick.value;
            if (!value) {
                start.value = "";
                end.value = "";
                timeLabel.textContent = quick.options[quick.selectedIndex].textContent;
                reset();
                return;
            }
            const days = value === "24h" ? 1 : Number.parseInt(value, 10);
            const now = new Date();
            start.value = localInput(new Date(now.getTime() - days * 86400000));
            end.value = localInput(now);
            timeLabel.textContent = quick.options[quick.selectedIndex].textContent;
            reset();
        });
        for (const button of quickButtons) {
            listen(button, "click", () => {
                quick.value = button.dataset.quickValue || "";
                quick.dispatchEvent(new Event("change", {bubbles: true}));
                timePopup.hidden = true;
                timeButton.setAttribute("aria-expanded", "false");
            });
        }
        listen(timeButton, "click", event => {
            event.stopPropagation();
            timePopup.hidden = !timePopup.hidden;
            timeButton.setAttribute("aria-expanded", String(!timePopup.hidden));
            positionPopover(timePopup, timeButton);
        });
        listen(applyButton, "click", () => {
            timeLabel.textContent = start.value || end.value ? labels.custom_range : labels.all_time;
            reset();
            timePopup.hidden = true;
            timeButton.setAttribute("aria-expanded", "false");
        });
        listen(tagButton, "click", event => {
            event.stopPropagation();
            tagPopup.hidden = !tagPopup.hidden;
            tagButton.setAttribute("aria-expanded", String(!tagPopup.hidden));
            positionPopover(tagPopup, tagButton);
        });
        listen(window, "resize", () => {
            layoutTable();
            repositionPopovers();
        });
        listen(window, "scroll", repositionPopovers, {capture: true, passive: true});
        listen(document, "click", event => {
            if (!root.contains(event.target) || !event.target.closest(".pagination-table-filter-group")) {
                tagPopup.hidden = true;
                tagButton.setAttribute("aria-expanded", "false");
            }
            if (!root.contains(event.target) || !event.target.closest(".pagination-table-time-group")) {
                timePopup.hidden = true;
                timeButton.setAttribute("aria-expanded", "false");
            }
        });
        instances.set(tableId, {controller, resizeObserver});
        render();
        initializeColumnWidths();
        fillLastColumn();
        layoutTable();
    };

    rootApi.paginationTable = {
        mount,
        destroy({tableId}) { destroy(tableId); },
    };
})();
