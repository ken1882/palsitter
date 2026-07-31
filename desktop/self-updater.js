const path = require('path');


function createSelfUpdater({ app, applicationRoot, backendRoot, spawnWithDebug }) {
  function gitPath() {
    if (process.env.PALSITTER_GIT) return process.env.PALSITTER_GIT;
    if (app.isPackaged) return path.join(applicationRoot(), 'git', 'cmd', 'git.exe');
    return process.platform === 'win32' ? 'git.exe' : 'git';
  }

  function refreshPackagedRepository() {
    if (!app.isPackaged) return Promise.resolve();
    // Run Git asynchronously so the splash window keeps painting while the
    // packaged repository index is refreshed.
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

  return {
    addBackendEnvironment(environment) {
      return { ...environment, PALSITTER_GIT: gitPath() };
    },
    refreshPackagedRepository,
  };
}


module.exports = { createSelfUpdater };
