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
// These are a FALLBACK, not a precedence order. Metro walks `node_modules`
// upward from the importing file first and only consults this list for what
// that walk missed — so it cannot make a nested copy win, and cannot dedupe a
// package npm installed twice. React is the case that matters: react-native
// imports it from the repo root while a screen imports it from `mobile/`, two
// Reacts end up in the bundle, and the first hook call throws "Invalid hook
// call" at launch while lint, tsc and vitest all stay green. The fix is to keep
// one copy in the tree — mobile and web pin the same react, and
// `reactDedupe.test.ts` fails if they ever drift apart again.
config.resolver.nodeModulesPaths = [
  path.resolve(projectRoot, "node_modules"),
  path.resolve(workspaceRoot, "node_modules"),
];

module.exports = config;
