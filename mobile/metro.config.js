// Metro in a workspace. npm hoists most dependencies to the repo root, so Metro
// has to watch and resolve one level above `mobile/` — without this it resolves
// react-native from mobile/node_modules only, finds nothing, and the failure
// reads as a missing package rather than a monorepo setup problem.
const path = require("path");

const { getDefaultConfig } = require("expo/metro-config");

const projectRoot = __dirname;
const workspaceRoot = path.resolve(projectRoot, "..");

const config = getDefaultConfig(projectRoot);

config.watchFolders = [workspaceRoot];
// Order matters: a nested copy must win over the hoisted one, so a package npm
// could not dedupe still resolves to the version mobile asked for. React is the
// live case — web pins 19.2.4 and Expo SDK 57 pins 19.2.3, so npm hoists web's
// and nests ours, and two React copies in one bundle is not a subtle failure.
config.resolver.nodeModulesPaths = [
  path.resolve(projectRoot, "node_modules"),
  path.resolve(workspaceRoot, "node_modules"),
];

module.exports = config;
