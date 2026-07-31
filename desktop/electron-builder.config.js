const build = require('./package.json').build;


module.exports = () => {
  const noUpdate = process.env.PALSITTER_BUILD_VARIANT === 'noupdate';
  if (!noUpdate) return build;
  return {
    ...build,
    files: build.files.filter((item) => item !== 'self-updater.js'),
    extraResources: build.extraResources.filter(
      (item) => item.to !== 'backend/.git' && item.to !== 'git',
    ),
  };
};
