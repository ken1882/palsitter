const { app, BrowserWindow, dialog, Menu, nativeImage, Tray } = require('electron');
const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const net = require('net');
const path = require('path');

const CONTROL_HOST = '127.0.0.1';
const DEFAULT_WEB_PORT = 22368;
const DEFAULT_CONTROL_PORT = 22369;
const SHUTDOWN_TIMEOUT_MS = 65_000;

let mainWindow = null;
let splashWindow = null;
let tray = null;
let backend = null;
let backendExited = false;
let backendReady = false;
let backendRestarting = false;
let exiting = false;
let allowWindowClose = false;
let controlToken = null;
let controlPort = null;
let webPort = null;
let webListenHost = CONTROL_HOST;
let webConnectHost = CONTROL_HOST;
let exitFinished = false;

function applicationRoot() {
  return app.isPackaged ? process.resourcesPath : path.resolve(__dirname, '..');
}

function configureDataPath() {
  if (app.isPackaged) {
    app.setPath('userData', path.join(path.dirname(process.execPath), 'data'));
  }
}

function backendRoot() {
  return app.isPackaged ? path.join(applicationRoot(), 'backend') : applicationRoot();
}

function pythonPath() {
  if (process.env.PALSITTER_PYTHON) return process.env.PALSITTER_PYTHON;
  if (app.isPackaged) return path.join(applicationRoot(), 'python', 'python.exe');
  return process.platform === 'win32' ? 'python.exe' : 'python3';
}

function gitPath() {
  if (process.env.PALSITTER_GIT) return process.env.PALSITTER_GIT;
  if (app.isPackaged) return path.join(applicationRoot(), 'git', 'cmd', 'git.exe');
  return process.platform === 'win32' ? 'git.exe' : 'git';
}

function debugLogDescriptor(component) {
  const dataRoot = app.getPath('userData');
  try {
    const settings = JSON.parse(fs.readFileSync(
      path.join(dataRoot, 'config', 'webui', 'settings.json'),
      'utf8',
    ));
    if (!settings || settings.debug_mode !== true) return null;
    const now = new Date();
    const date = [now.getFullYear(), now.getMonth() + 1, now.getDate()]
      .map((value) => String(value).padStart(2, '0')).join('');
    const directory = path.join(dataRoot, 'config', 'webui', 'debug');
    fs.mkdirSync(directory, { recursive: true });
    return fs.openSync(path.join(directory, `${component}-${date}.log`), 'a');
  } catch (_) {
    return null;
  }
}

function spawnWithDebug(component, executable, args, options = {}) {
  const descriptor = debugLogDescriptor(component);
  let child;
  try {
    child = spawn(executable, args, {
      ...options,
      stdio: descriptor === null ? 'ignore' : ['ignore', descriptor, descriptor],
    });
  } catch (error) {
    if (descriptor !== null) fs.closeSync(descriptor);
    throw error;
  }
  if (descriptor !== null) {
    let closed = false;
    const closeDescriptor = () => {
      if (closed) return;
      closed = true;
      try {
        fs.closeSync(descriptor);
      } catch (_) {
        // The child may have already released the inherited descriptor.
      }
    };
    child.once('close', closeDescriptor);
    child.once('error', closeDescriptor);
  }
  return child;
}

function spawnSyncWithDebug(component, executable, args, options = {}) {
  const descriptor = debugLogDescriptor(component);
  try {
    return spawnSync(executable, args, {
      ...options,
      stdio: descriptor === null ? 'ignore' : ['ignore', descriptor, descriptor],
    });
  } finally {
    if (descriptor !== null) {
      try {
        fs.closeSync(descriptor);
      } catch (_) {
        // The child may have already released the inherited descriptor.
      }
    }
  }
}

function refreshPackagedRepository() {
  if (!app.isPackaged) return Promise.resolve();
  // Run git asynchronously so the splash window keeps painting instead of
  // freezing the main process while the packaged repository is refreshed.
  return new Promise((resolve) => {
    const child = spawnWithDebug('desktop-git', gitPath(), [
      '-c',
      `safe.directory=${path.resolve(backendRoot())}`,
      '-C',
      backendRoot(),
      'update-index',
      '--refresh',
    ], { windowsHide: true });
    child.once('exit', () => resolve());
    child.once('error', () => resolve());
  });
}

function configuredWebHost(dataRoot) {
  const candidate = process.env.PALSITTER_HOST;
  if (candidate) return candidate;
  try {
    const data = JSON.parse(fs.readFileSync(
      path.join(dataRoot, 'config', 'webui', 'settings.json'),
      'utf8',
    ));
    if (data && typeof data.bind_address === 'string' && data.bind_address) {
      return data.bind_address;
    }
  } catch (_) {
    // Use the loopback default when settings have not been created yet.
  }
  return CONTROL_HOST;
}

function connectionHost(listenHost) {
  return listenHost === '0.0.0.0' ? CONTROL_HOST : listenHost;
}

function reservePort(preferred, host = CONTROL_HOST) {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(preferred, host, () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
  });
}

async function reserveRestartPort(preferred, host = CONTROL_HOST) {
  try {
    return await reservePort(preferred, host);
  } catch (error) {
    if (error.code !== 'EADDRINUSE') throw error;
    return reservePort(0, host);
  }
}

class StartupCancelledError extends Error {}

function desktopLocale() {
  const locale = String(app.getLocale() || 'en-US').toLowerCase();
  if (locale.startsWith('zh')) return 'zh-TW';
  if (locale.startsWith('ja')) return 'ja-JP';
  return 'en-US';
}

function startupText(key, values = {}) {
  const keyName = `startup.${key}`;
  let text;
  for (const language of [desktopLocale(), 'en-US']) {
    try {
      const localePath = path.join(backendRoot(), 'module', 'webui', 'locales', `${language}.json`);
      const catalog = JSON.parse(fs.readFileSync(localePath, 'utf8'));
      text = catalog[keyName];
    } catch (_) {
      text = null;
    }
    if (text) break;
  }
  text = text || keyName;
  for (const [name, value] of Object.entries(values)) {
    text = text.replaceAll(`{${name}}`, String(value));
  }
  return text;
}

function killPort(port) {
  const result = spawnSyncWithDebug(
    'desktop-kill-port',
    pythonPath(),
    [path.join(backendRoot(), 'gui.py'), '--kill-port', String(port)],
    { cwd: backendRoot(), windowsHide: true },
  );
  return !result.error && result.status === 0;
}

function forceKillBackend() {
  if (!backend || backendExited) return;
  if (process.platform === 'win32' && backend.pid) {
    spawnSyncWithDebug('desktop-taskkill', 'taskkill.exe', ['/PID', String(backend.pid), '/T', '/F'], {
      windowsHide: true,
    });
    return;
  }
  try {
    backend.kill('SIGKILL');
  } catch (_) {
    // The backend may have exited between the state check and the kill.
  }
}

async function reservePortWithPrompt(preferred, host = CONTROL_HOST) {
  try {
    return await reservePort(preferred, host);
  } catch (error) {
    if (error.code !== 'EADDRINUSE') throw error;
  }

  const killResult = await dialog.showMessageBox(mainWindow, {
    type: 'warning',
    buttons: [startupText('no'), startupText('kill')],
    defaultId: 0,
    cancelId: 0,
    title: startupText('conflictTitle'),
    message: startupText('conflictMessage', { port: preferred }),
    detail: startupText('conflictDetail'),
  });
  if (killResult.response === 1 && killPort(preferred)) {
    try {
      return await reservePort(preferred, host);
    } catch (_) {
      // The process may still hold the port or another process may have won
      // the race. Offer the alternate-port path below.
    }
  }

  const alternateResult = await dialog.showMessageBox(mainWindow, {
    type: 'warning',
    buttons: [startupText('exit'), startupText('useAlternate')],
    defaultId: 0,
    cancelId: 0,
    title: startupText('alternateTitle'),
    message: startupText('alternateMessage', { port: preferred }),
    detail: startupText('alternateDetail'),
  });
  if (alternateResult.response !== 1) throw new StartupCancelledError();
  return reservePort(0, host);
}

async function waitForBackend(url) {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok || response.status < 500) return;
    } catch (_) {
      // The backend is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  throw new Error(`Palsitter backend did not become ready at ${url}`);
}

function buildEnvironment(dataRoot) {
  const backend = backendRoot();
  return {
    ...process.env,
    PALSITTER_CONFIG_DIR: path.join(dataRoot, 'config'),
    PALSITTER_PROFILE_DIR: path.join(dataRoot, 'profile'),
    PALSITTER_LOG_DIR: path.join(dataRoot, 'logs'),
    PALSITTER_BACKEND_DIR: backend,
    PALSITTER_GIT: gitPath(),
    PYTHONPATH: process.env.PYTHONPATH ? `${backend}${path.delimiter}${process.env.PYTHONPATH}` : backend,
    PALSITTER_DESKTOP_TOKEN: controlToken,
  };
}

function showWindow() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

function toggleWindow() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  if (mainWindow.isVisible()) mainWindow.hide();
  else showWindow();
}

function createTray() {
  const iconPath = path.join(__dirname, 'assets', 'palsitter.png');
  const icon = nativeImage.createFromPath(iconPath);
  tray = new Tray(icon);
  tray.setToolTip('Palsitter');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Open Web UI', click: showWindow },
    { type: 'separator' },
    { label: 'Exit Palsitter', click: requestExit },
  ]));
  tray.on('click', toggleWindow);
}

function createSplashWindow() {
  splashWindow = new BrowserWindow({
    width: 420,
    height: 260,
    frame: false,
    resizable: false,
    center: true,
    show: false,
    transparent: true,
    backgroundColor: '#00000000',
    skipTaskbar: true,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  splashWindow.once('ready-to-show', () => {
    if (splashWindow && !splashWindow.isDestroyed()) splashWindow.show();
  });
  splashWindow.loadFile(path.join(__dirname, 'assets', 'splash.html'), {
    query: { message: startupText('starting') },
  });
}

function closeSplash() {
  if (splashWindow && !splashWindow.isDestroyed()) splashWindow.destroy();
  splashWindow = null;
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 960,
    minHeight: 640,
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  mainWindow.webContents.session.webRequest.onBeforeSendHeaders(
    { urls: ['http://*/*'] },
    (details, callback) => {
      const expected = `http://${webConnectHost}:${webPort}/`;
      if (controlToken && details.url.startsWith(expected)) {
        details.requestHeaders['X-Palsitter-Desktop-Token'] = controlToken;
      }
      callback({ requestHeaders: details.requestHeaders });
    },
  );
  mainWindow.on('close', (event) => {
    if (!allowWindowClose) {
      event.preventDefault();
      mainWindow.hide();
    }
  });
  mainWindow.once('ready-to-show', () => {
    closeSplash();
    showWindow();
  });
}

async function requestExit() {
  if (exiting) return;
  const result = await dialog.showMessageBox(mainWindow, {
    type: 'warning',
    buttons: ['Cancel', 'GUI only', 'Stop all'],
    defaultId: 0,
    cancelId: 0,
    title: 'Exit Palsitter',
    message: 'How should Palsitter exit?',
    detail: 'GUI only leaves agents and game servers running. Stop all saves state and gracefully stops them before closing the GUI.',
  });
  if (result.response === 0) return;

  exiting = true;
  if (result.response === 1) {
    void performGuiOnlyShutdown();
  } else {
    showWindow();
    void performGracefulShutdown();
  }
}

function waitForBackendExit(timeoutMs = SHUTDOWN_TIMEOUT_MS + 10_000) {
  return new Promise((resolve, reject) => {
    const deadline = setTimeout(() => reject(new Error('GUI backend did not exit')), timeoutMs);
    if (backendExited) {
      clearTimeout(deadline);
      resolve();
    } else if (backend) {
      backend.once('exit', () => {
        clearTimeout(deadline);
        resolve();
      });
    } else {
      clearTimeout(deadline);
      resolve();
    }
  });
}

async function performGuiOnlyShutdown() {
  try {
    if (controlPort == null || !controlToken) throw new Error('GUI control endpoint unavailable');
    const response = await fetch(`http://${CONTROL_HOST}:${controlPort}/desktop/gui-only`, {
      method: 'POST',
      headers: { 'X-Palsitter-Token': controlToken },
    });
    const body = await response.json();
    if (!response.ok || !body.ok) throw new Error(body.error || 'GUI shutdown request failed');
    await waitForBackendExit();
    finishExit();
  } catch (_) {
    forceKillBackend();
    finishExit();
  }
}

async function forceExitAfterShutdownFailure() {
  if (controlPort != null && controlToken) {
    try {
      const response = await fetch(`http://${CONTROL_HOST}:${controlPort}/desktop/force-shutdown`, {
        method: 'POST',
        headers: { 'X-Palsitter-Token': controlToken },
      });
      const body = await response.json();
      if (response.ok && body.ok) {
        await waitForBackendExit();
        finishExit();
        return;
      }
    } catch (_) {
      // Fall through to terminating the backend process tree.
    }
  }
  forceKillBackend();
  finishExit();
}

async function performGracefulShutdown() {
  try {
    if (controlPort == null || !controlToken) throw new Error('GUI control endpoint unavailable');
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), SHUTDOWN_TIMEOUT_MS);
    let response;
    try {
      response = await fetch(`http://${CONTROL_HOST}:${controlPort}/desktop/shutdown`, {
        method: 'POST',
        headers: { 'X-Palsitter-Token': controlToken },
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timer);
    }
    const body = await response.json();
    if (!response.ok || !body.ok) throw new Error(body.error || 'Shutdown request failed');
    await waitForBackendExit();
    finishExit();
  } catch (_) {
    await forceExitAfterShutdownFailure();
  }
}

function finishExit() {
  if (exitFinished) return;
  exitFinished = true;
  allowWindowClose = true;
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.close();
  if (tray && !tray.isDestroyed()) tray.destroy();
  app.quit();
}

async function startBackend({ restarting = false } = {}) {
  const reserve = restarting ? reserveRestartPort : reservePortWithPrompt;
  const dataRoot = app.getPath('userData');
  webListenHost = configuredWebHost(dataRoot);
  webConnectHost = connectionHost(webListenHost);
  webPort = await reserve(
    Number(restarting ? webPort : (process.env.PALSITTER_PORT || DEFAULT_WEB_PORT)),
    webListenHost,
  );
  controlPort = await reserve(
    Number(restarting ? controlPort : (process.env.PALSITTER_CONTROL_PORT || DEFAULT_CONTROL_PORT)),
    CONTROL_HOST,
  );
  controlToken = require('crypto').randomBytes(32).toString('hex');
  const args = [
    path.join(backendRoot(), 'gui.py'),
    '--desktop-server',
    '--host', webListenHost,
    '--port', String(webPort),
    '--control-port', String(controlPort),
  ];
  backend = spawnWithDebug('desktop-backend', pythonPath(), args, {
    cwd: backendRoot(),
    env: buildEnvironment(dataRoot),
    windowsHide: true,
  });
  backendExited = false;
  backendReady = false;
  const child = backend;
  backend.on('exit', () => {
    if (backend !== child) return;
    backendExited = true;
  });
  backend.on('close', (code) => {
    if (backend !== child) return;
    if (code === 75 && !exiting && !backendRestarting) {
      void restartBackend();
      return;
    }
    // The backend stopped on its own — typically the in-app "Shutdown
    // Palsitter" action, which stops the web server directly. Nothing is
    // left to supervise, so exit the desktop process immediately instead of
    // lingering with a dead backend. Guard on backendReady so a crash during
    // startup still surfaces the startup error dialog rather than exiting
    // silently.
    if (backendReady && !exiting && !backendRestarting) {
      exiting = true;
      exitFinished = true;
      app.exit(0);
    }
  });
  backend.on('error', (error) => {
    if (!exiting) dialog.showErrorBox('Palsitter backend failed', String(error));
  });
  await waitForBackend(`http://${webConnectHost}:${webPort}/`);
  backendReady = true;
}

async function restartBackend() {
  if (exiting || backendRestarting) return;
  backendRestarting = true;
  try {
    await startBackend({ restarting: true });
    await mainWindow.loadURL(`http://${webConnectHost}:${webPort}/`);
  } catch (error) {
    dialog.showErrorBox(
      startupText('errorTitle'),
      String(error.message || error),
    );
    app.exit(1);
  } finally {
    backendRestarting = false;
  }
}

async function main() {
  configureDataPath();
  if (!app.requestSingleInstanceLock()) {
    app.quit();
    return;
  }
  app.on('second-instance', showWindow);
  await app.whenReady();
  createSplashWindow();
  createWindow();
  createTray();
  try {
    await refreshPackagedRepository();
    await startBackend();
    await mainWindow.loadURL(`http://${webConnectHost}:${webPort}/`);
  } catch (error) {
    closeSplash();
    if (!(error instanceof StartupCancelledError)) {
      dialog.showErrorBox(
        startupText('errorTitle'),
        String(error.message || error),
      );
    }
    app.quit();
  }
}

app.on('before-quit', (event) => {
  if (!exiting && backend && !backendExited) {
    event.preventDefault();
    requestExit();
  }
});

main().catch((error) => {
  closeSplash();
  if (!(error instanceof StartupCancelledError)) {
    dialog.showErrorBox(startupText('errorTitle'), String(error.message || error));
  }
  app.quit();
});
