(() => {
"use strict";
const root = window.Palsitter = window.Palsitter || {};
root.palworld = root.palworld || {};
const api = root.palworld.map = root.palworld.map || {};
let refreshTimer = null;
let refreshStartTimer = null;
let controller = null;

api.mount = ({mapSize, initialScale, labels, generation}) => {
    api.generation = generation;
    const oldDestroy = api.destroy;
    const previousDropdownOpen = api.playerDropdownOpen === true;
    if (oldDestroy) oldDestroy();
    controller = new AbortController();
    const signal = controller.signal;
    const viewport = document.getElementById('palworld-map-viewport');
    const world = document.getElementById('palworld-map-world');
    const playerLayer = document.getElementById('palworld-map-players');
    const playerButton = document.getElementById('palworld-map-player-button');
    const playerDropdown = document.getElementById('palworld-map-player-dropdown');
    const playerList = document.getElementById('palworld-map-player-list');
    const controls = document.querySelectorAll('#palworld-map-top-left, #palworld-map-top-right, .palworld-map-zoom-controls');
    const mapState = {
        size: mapSize,
        scale: initialScale,
        minScale: 0.0625,
        maxScale: 1,
        centerX: mapSize / 2,
        centerY: mapSize / 2,
        activeMap: 'palpagos',
        allPlayers: [],
        players: [],
        selected: null,
        playerDropdownOpen: previousDropdownOpen,
        dragging: false,
        pointerId: null,
        lastPointerX: 0,
        lastPointerY: 0,
    };
    
    const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
    const mapLayers = [...document.querySelectorAll('.palworld-map-layer')];
    const mapSelect = document.getElementById('palworld-map-select');
    const playersForActiveMap = () => mapState.allPlayers.filter(
        player => player.map === mapState.activeMap
    );
    const renderActiveMap = () => {
        for (const layer of mapLayers) layer.hidden = layer.dataset.mapName !== mapState.activeMap;
        viewport.dataset.mapName = mapState.activeMap;
    };
    const setActiveMap = mapName => {
        if (!mapLayers.some(layer => layer.dataset.mapName === mapName)) return;
        mapState.activeMap = mapName;
        mapState.centerX = mapState.size / 2;
        mapState.centerY = mapState.size / 2;
        mapState.scale = initialScale;
        mapState.selected = null;
        mapSelect.value = mapName;
        renderActiveMap();
        updatePlayers(mapState.allPlayers, mapState.playerState);
    };
    const viewportCenter = () => ({
        x: viewport.clientWidth / 2,
        y: viewport.clientHeight / 2,
    });
    const clampCamera = () => {
        const halfWidth = viewport.clientWidth / (2 * mapState.scale);
        const halfHeight = viewport.clientHeight / (2 * mapState.scale);
        mapState.centerX = halfWidth * 2 >= mapState.size
            ? mapState.size / 2
            : clamp(mapState.centerX, halfWidth, mapState.size - halfWidth);
        mapState.centerY = halfHeight * 2 >= mapState.size
            ? mapState.size / 2
            : clamp(mapState.centerY, halfHeight, mapState.size - halfHeight);
    };
    const applyCamera = () => {
        clampCamera();
        const center = viewportCenter();
        world.style.transform = `translate(${center.x - mapState.centerX * mapState.scale}px, ${center.y - mapState.centerY * mapState.scale}px) scale(${mapState.scale})`;
        const inverseScale = 1 / mapState.scale;
        for (const marker of document.querySelectorAll('.palworld-map-poi-wrap, .palworld-map-player-dot')) {
            marker.style.transform = `translate(-50%, -50%) scale(${inverseScale})`;
        }
        viewport.dataset.zoom = String(mapState.scale);
        viewport.dataset.cameraCenterX = String(mapState.centerX);
        viewport.dataset.cameraCenterY = String(mapState.centerY);
    };
    const screenToWorld = (clientX, clientY) => {
        const rect = viewport.getBoundingClientRect();
        const center = viewportCenter();
        return {
            x: mapState.centerX + (clientX - rect.left - center.x) / mapState.scale,
            y: mapState.centerY + (clientY - rect.top - center.y) / mapState.scale,
        };
    };
    const setScale = (nextScale, clientX = null, clientY = null) => {
        const oldScale = mapState.scale;
        const anchor = clientX === null ? null : screenToWorld(clientX, clientY);
        mapState.scale = clamp(nextScale, mapState.minScale, mapState.maxScale);
        if (anchor) {
            const rect = viewport.getBoundingClientRect();
            const center = viewportCenter();
            mapState.centerX = anchor.x - (clientX - rect.left - center.x) / mapState.scale;
            mapState.centerY = anchor.y - (clientY - rect.top - center.y) / mapState.scale;
        } else if (oldScale !== mapState.scale) {
            clampCamera();
        }
        applyCamera();
    };
    const closePlayerDropdown = () => {
        mapState.playerDropdownOpen = false;
        api.playerDropdownOpen = false;
        playerDropdown.hidden = true;
        playerButton.setAttribute('aria-expanded', 'false');
    };
    const openPlayerDropdown = () => {
        mapState.playerDropdownOpen = true;
        api.playerDropdownOpen = true;
        playerDropdown.hidden = false;
        playerButton.setAttribute('aria-expanded', 'true');
    };
    const centerOnPlayer = (userid) => {
        const player = mapState.players.find(candidate => candidate.userId === userid);
        if (!player || !player.valid) return;
        mapState.selected = userid;
        const edgeDistanceX = Math.max(1, Math.min(player.x, mapState.size - player.x));
        const edgeDistanceY = Math.max(1, Math.min(player.y, mapState.size - player.y));
        const requiredScale = Math.max(
            viewport.clientWidth / (2 * edgeDistanceX),
            viewport.clientHeight / (2 * edgeDistanceY),
            mapState.scale,
        );
        mapState.scale = clamp(requiredScale, mapState.minScale, mapState.maxScale);
        mapState.centerX = player.x;
        mapState.centerY = player.y;
        applyCamera();
        closePlayerDropdown();
        updatePlayerList();
    };
    const updatePlayerList = () => {
        const scrollTop = playerList.scrollTop;
        playerList.replaceChildren();
        if (!mapState.players.length) {
            const empty = document.createElement('div');
            empty.className = 'palworld-map-player-empty';
            empty.textContent = labels.no_players;
            playerList.appendChild(empty);
        }
        for (const player of mapState.players) {
            const row = document.createElement('button');
            row.type = 'button';
            row.className = 'palworld-map-player-row';
            row.dataset.playerId = player.userId;
            row.disabled = !player.valid;
            row.classList.toggle('selected', player.userId === mapState.selected);
            const name = document.createElement('span');
            name.className = 'palworld-map-player-name';
            name.textContent = `${player.name} (Lv. ${player.level})`;
            row.append(name);
            row.addEventListener('click', () => centerOnPlayer(player.userId));
            playerList.appendChild(row);
        }
        playerList.scrollTop = scrollTop;
    };
    const updatePlayers = (players, state) => {
        const dropdownWasOpen = !playerDropdown.hidden
            || mapState.playerDropdownOpen
            || api.playerDropdownOpen === true;
        mapState.allPlayers = Array.isArray(players) ? players : [];
        mapState.playerState = state;
        mapState.players = playersForActiveMap();
        playerButton.textContent = labels.player_count.replace('{count}', String(mapState.players.length));
        playerLayer.replaceChildren();
        for (const player of mapState.players) {
            if (!player.valid) continue;
            const dot = document.createElement('div');
            dot.className = 'palworld-map-player-dot';
            dot.dataset.playerId = player.userId;
            dot.setAttribute('role', 'img');
            dot.setAttribute('aria-label', labels.player_aria.replace('{name}', player.name));
            dot.dataset.playerName = player.name;
            dot.title = player.name;
            dot.style.left = `${player.x}px`;
            dot.style.top = `${player.y}px`;
            playerLayer.appendChild(dot);
        }
        updatePlayerList();
        if (dropdownWasOpen) openPlayerDropdown();
        else closePlayerDropdown();
        applyCamera();
    };
    const onPointerDown = event => {
        if (event.button !== 0) return;
        mapState.dragging = true;
        mapState.pointerId = event.pointerId;
        mapState.lastPointerX = event.clientX;
        mapState.lastPointerY = event.clientY;
        viewport.setPointerCapture(event.pointerId);
        viewport.classList.add('dragging');
    };
    const onPointerMove = event => {
        if (!mapState.dragging || event.pointerId !== mapState.pointerId) return;
        mapState.centerX -= (event.clientX - mapState.lastPointerX) / mapState.scale;
        mapState.centerY -= (event.clientY - mapState.lastPointerY) / mapState.scale;
        mapState.lastPointerX = event.clientX;
        mapState.lastPointerY = event.clientY;
        applyCamera();
    };
    const onPointerUp = event => {
        if (event.pointerId !== mapState.pointerId) return;
        mapState.dragging = false;
        mapState.pointerId = null;
        viewport.classList.remove('dragging');
    };
    const onWheel = event => {
        event.preventDefault();
        setScale(mapState.scale * (event.deltaY < 0 ? 1.25 : 0.8), event.clientX, event.clientY);
    };
    const onOutsideClick = event => {
        if (event.target.closest?.('#pywebio-scope-map_refresh')) return;
        if (!playerDropdown.hidden && !event.target.closest('#palworld-map-top-right')) closePlayerDropdown();
    };
    const onKeyDown = event => {
        if (event.key === 'Escape') closePlayerDropdown();
    };
    viewport.addEventListener('pointerdown', onPointerDown, {signal});
    viewport.addEventListener('pointermove', onPointerMove, {signal});
    viewport.addEventListener('pointerup', onPointerUp, {signal});
    viewport.addEventListener('pointercancel', onPointerUp, {signal});
    viewport.addEventListener('wheel', onWheel, {passive: false, signal});
    playerButton.addEventListener('click', event => {
        event.stopPropagation();
        playerDropdown.hidden ? openPlayerDropdown() : closePlayerDropdown();
    }, {signal});
    mapSelect.addEventListener('change', event => setActiveMap(event.target.value), {signal});
    document.addEventListener('click', onOutsideClick, {signal});
    document.addEventListener('keydown', onKeyDown, {signal});
    document.getElementById('palworld-map-zoom-in').addEventListener('click', () => setScale(mapState.scale * 1.25), {signal});
    document.getElementById('palworld-map-zoom-out').addEventListener('click', () => setScale(mapState.scale * 0.8), {signal});
    for (const control of controls) control.addEventListener('pointerdown', event => event.stopPropagation(), {signal});
    api.updatePlayers = updatePlayers;
    api.destroy = () => {
        controller?.abort();
        controller = null;
        if (refreshStartTimer) clearTimeout(refreshStartTimer);
        refreshStartTimer = null;
        delete api.updatePlayers;
        delete api.destroy;
    };
    renderActiveMap();
    applyCamera();
    if (previousDropdownOpen) openPlayerDropdown();
    else closePlayerDropdown();
    document.querySelectorAll("[data-map-left]").forEach(element => {
        element.style.left = `${element.dataset.mapLeft}px`;
        element.style.top = `${element.dataset.mapTop}px`;
        if (element.dataset.mapWidth) element.style.width = `${element.dataset.mapWidth}px`;
        if (element.dataset.mapHeight) element.style.height = `${element.dataset.mapHeight}px`;
    });
};

api.startRefresh = () => {
    if (refreshTimer) clearInterval(refreshTimer);
    if (refreshStartTimer) clearTimeout(refreshStartTimer);
    const refresh = () => document.querySelector("#pywebio-scope-map_refresh button")?.click();
    refreshTimer = setInterval(refresh, 1000);
    refreshStartTimer = setTimeout(() => {
        refreshStartTimer = null;
        refresh();
    }, 0);
};

api.destroyPage = () => {
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = null;
    api.destroy?.();
    api.playerDropdownOpen = false;
    document.getElementById("pywebio-scope-content")?.classList.remove("map-content");
};

api.pushPlayers = ({players, state, generation}) => {
    if (generation != null && generation !== api.generation) return;
    api.updatePlayers?.(players, state);
};
})();
