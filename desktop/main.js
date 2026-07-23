const { app, BrowserWindow, dialog, Menu, nativeImage, Tray } = require('electron');
const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const net = require('net');
const path = require('path');

const WEB_HOST = '127.0.0.1';
const DEFAULT_WEB_PORT = 22368;
const DEFAULT_CONTROL_PORT = 22369;
const SHUTDOWN_TIMEOUT_MS = 65_000;

let mainWindow = null;
let tray = null;
let backend = null;
let backendExited = false;
let backendRestarting = false;
let exiting = false;
let allowWindowClose = false;
let controlToken = null;
let controlPort = null;
let webPort = null;
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

function refreshPackagedRepository() {
  if (!app.isPackaged) return;
  spawnSync(gitPath(), ['-C', backendRoot(), 'update-index', '--refresh'], {
    windowsHide: true,
    stdio: 'ignore',
  });
}

function reservePort(preferred) {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(preferred, WEB_HOST, () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
  });
}

async function reserveRestartPort(preferred) {
  try {
    return await reservePort(preferred);
  } catch (error) {
    if (error.code !== 'EADDRINUSE') throw error;
    return reservePort(0);
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
  const result = spawnSync(
    pythonPath(),
    [path.join(backendRoot(), 'gui.py'), '--kill-port', String(port)],
    { cwd: backendRoot(), windowsHide: true, stdio: 'ignore' },
  );
  return !result.error && result.status === 0;
}

async function reservePortWithPrompt(preferred) {
  try {
    return await reservePort(preferred);
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
      return await reservePort(preferred);
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
  return reservePort(0);
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
  mainWindow.on('close', (event) => {
    if (!allowWindowClose) {
      event.preventDefault();
      mainWindow.hide();
    }
  });
  mainWindow.once('ready-to-show', showWindow);
}

async function requestExit() {
  if (exiting) return;
  const result = await dialog.showMessageBox(mainWindow, {
    type: 'warning',
    buttons: ['Cancel', 'Exit Palsitter'],
    defaultId: 0,
    cancelId: 0,
    title: 'Exit Palsitter',
    message: 'Exit Palsitter and stop all active servers?',
    detail: 'Palsitter will save state, gracefully stop every agent and game server, then close the GUI.',
  });
  if (result.response !== 1) return;

  exiting = true;
  showWindow();
  void performGracefulShutdown();
}

function waitForBackendExit(timeoutMs = SHUTDOWN_TIMEOUT_MS + 10_000) {
  return new Promise((resolve, reject) => {
    const deadline = setTimeout(() => reject(new Error('GUI backend did not exit')), timeoutMs);
    if (backendExited) {
      clearTimeout(deadline);
      resolve();
    } else if (backend) {
      backend.once('close', () => {
        clearTimeout(deadline);
        resolve();
      });
    } else {
      clearTimeout(deadline);
      resolve();
    }
  });
}

async function performGracefulShutdown() {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), SHUTDOWN_TIMEOUT_MS);
    let response;
    try {
      response = await fetch(`http://${WEB_HOST}:${controlPort}/desktop/shutdown`, {
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
  } catch (error) {
    exiting = false;
    await dialog.showMessageBox(mainWindow, {
      type: 'error',
      title: 'Palsitter shutdown failed',
      message: 'Palsitter is still running.',
      detail: String(error.message || error),
    });
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
  webPort = await reserve(
    Number(restarting ? webPort : (process.env.PALSITTER_PORT || DEFAULT_WEB_PORT)),
  );
  controlPort = await reserve(
    Number(restarting ? controlPort : (process.env.PALSITTER_CONTROL_PORT || DEFAULT_CONTROL_PORT)),
  );
  controlToken = require('crypto').randomBytes(32).toString('hex');
  const dataRoot = app.getPath('userData');
  const args = [
    path.join(backendRoot(), 'gui.py'),
    '--desktop-server',
    '--host', WEB_HOST,
    '--port', String(webPort),
    '--control-port', String(controlPort),
  ];
  backend = spawn(pythonPath(), args, {
    cwd: backendRoot(),
    env: buildEnvironment(dataRoot),
    windowsHide: true,
    stdio: ['ignore', 'ignore', 'ignore'],
  });
  backendExited = false;
  const child = backend;
  backend.on('close', (code) => {
    if (backend !== child) return;
    backendExited = true;
    if (code === 75 && !exiting && !backendRestarting) {
      void restartBackend();
    }
  });
  backend.on('error', (error) => {
    if (!exiting) dialog.showErrorBox('Palsitter backend failed', String(error));
  });
  await waitForBackend(`http://${WEB_HOST}:${webPort}/`);
}

async function restartBackend() {
  if (exiting || backendRestarting) return;
  backendRestarting = true;
  try {
    await startBackend({ restarting: true });
    await mainWindow.loadURL(`http://${WEB_HOST}:${webPort}/`);
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
  refreshPackagedRepository();
  createWindow();
  createTray();
  try {
    await startBackend();
    await mainWindow.loadURL(`http://${WEB_HOST}:${webPort}/`);
  } catch (error) {
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
  if (!(error instanceof StartupCancelledError)) {
    dialog.showErrorBox(startupText('errorTitle'), String(error.message || error));
  }
  app.quit();
});
