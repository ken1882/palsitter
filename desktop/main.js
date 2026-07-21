const { app, BrowserWindow, dialog, Menu, nativeImage, Tray } = require('electron');
const { spawn } = require('child_process');
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
let exiting = false;
let allowWindowClose = false;
let controlToken = null;
let controlPort = null;
let webPort = null;

function applicationRoot() {
  return app.isPackaged ? process.resourcesPath : path.resolve(__dirname, '..');
}

function backendRoot() {
  return app.isPackaged ? path.join(applicationRoot(), 'backend') : applicationRoot();
}

function pythonPath() {
  if (process.env.PALSITTER_PYTHON) return process.env.PALSITTER_PYTHON;
  if (app.isPackaged) return path.join(applicationRoot(), 'python', 'python.exe');
  return process.platform === 'win32' ? 'python.exe' : 'python3';
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
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.setEnabled(false);
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), SHUTDOWN_TIMEOUT_MS);
    const response = await fetch(`http://${WEB_HOST}:${controlPort}/desktop/shutdown`, {
      method: 'POST',
      headers: { 'X-Palsitter-Token': controlToken },
      signal: controller.signal,
    });
    clearTimeout(timer);
    const body = await response.json();
    if (!response.ok || !body.ok) {
      const names = Object.entries(body.instances || {})
        .filter(([, item]) => item.status !== 'stopped')
        .map(([name, item]) => `${name}: ${item.message}`)
        .join('\n');
      throw new Error(`${body.error || 'Graceful shutdown failed'}${names ? `\n${names}` : ''}`);
    }
    await new Promise((resolve, reject) => {
      const deadline = setTimeout(() => reject(new Error('GUI backend did not exit')), 10_000);
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
    allowWindowClose = true;
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.close();
    if (tray && !tray.isDestroyed()) tray.destroy();
    app.quit();
  } catch (error) {
    exiting = false;
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.setEnabled(true);
    await dialog.showMessageBox(mainWindow, {
      type: 'error',
      title: 'Palsitter shutdown failed',
      message: 'Palsitter is still running.',
      detail: String(error.message || error),
    });
  }
}

async function startBackend() {
  webPort = await reservePort(Number(process.env.PALSITTER_PORT || DEFAULT_WEB_PORT));
  controlPort = await reservePort(Number(process.env.PALSITTER_CONTROL_PORT || DEFAULT_CONTROL_PORT));
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
  backend.on('close', () => { backendExited = true; });
  backend.on('error', (error) => {
    if (!exiting) dialog.showErrorBox('Palsitter backend failed', String(error));
  });
  await waitForBackend(`http://${WEB_HOST}:${webPort}/`);
}

async function main() {
  if (!app.requestSingleInstanceLock()) {
    app.quit();
    return;
  }
  app.on('second-instance', showWindow);
  await app.whenReady();
  createWindow();
  createTray();
  try {
    await startBackend();
    await mainWindow.loadURL(`http://${WEB_HOST}:${webPort}/`);
  } catch (error) {
    dialog.showErrorBox('Palsitter could not start', String(error.message || error));
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
  dialog.showErrorBox('Palsitter could not start', String(error.message || error));
  app.quit();
});
