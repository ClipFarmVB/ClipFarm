// Flat config, matching web/'s setup rather than `expo lint`, which offers to
// write this file on first run and is therefore not something CI should invoke.
const expoConfig = require("eslint-config-expo/flat");

module.exports = [
  ...expoConfig,
  {
    ignores: ["dist/*", "ios/*", "android/*", ".expo/*"],
  },
];
