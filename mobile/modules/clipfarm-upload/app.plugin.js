/**
 * Config plugin for the background-upload module (CF-315).
 *
 * It is a no-op today, and it exists anyway: it is what keeps `ios/` and
 * `android/` out of git. Everything the native halves need from the generated
 * projects — the iOS background-modes entitlement (CF-323), the Android
 * `dataSync` foreground-service type and its permissions (CF-324) — is
 * expressed here and re-applied by `expo prebuild`, so neither ticket has to
 * hand-edit a generated project and commit it to make its change stick.
 *
 * Registered in app.json's `plugins`. CF-323 and CF-324 each add their own
 * platform's `withInfoPlist` / `withAndroidManifest` call below; the two edit
 * different lines of this file, which is the one place they overlap.
 */

/** @type {import('expo/config-plugins').ConfigPlugin} */
const withClipFarmUpload = (config) => config;

module.exports = withClipFarmUpload;
