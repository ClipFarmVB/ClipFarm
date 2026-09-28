/**
 * The upload module a screen should import (CF-315).
 *
 * Resolves to the native implementation when one is linked — CF-323 on iOS,
 * CF-324 on Android — and to the JS mock otherwise, so CF-330 can be built and
 * demoed before either lands and still runs unchanged afterwards.
 *
 * The native halves register under `NATIVE_MODULE_NAME` and emit
 * `UPLOAD_EVENT` with an `UploadTask` payload. That, plus the method names on
 * `BackgroundUploadModule`, is the whole native-side contract.
 */
import { requireOptionalNativeModule } from "expo";

import { createMockBackgroundUpload } from "./mock";
import type { BackgroundUploadModule, UploadTask } from "./types";
import { NATIVE_MODULE_NAME, UPLOAD_EVENT } from "./types";

/** The shape expo-modules-core exposes for a module declared in Swift/Kotlin. */
interface NativeUploadModule {
  startUpload(request: unknown): Promise<UploadTask>;
  cancelUpload(uploadId: string): Promise<void>;
  getUpload(uploadId: string): Promise<UploadTask | null>;
  getUploads(): Promise<UploadTask[]>;
  addListener(
    event: string,
    listener: (task: UploadTask) => void
  ): { remove(): void };
}

const nativeModule = requireOptionalNativeModule<NativeUploadModule>(NATIVE_MODULE_NAME);

function wrapNative(module: NativeUploadModule): BackgroundUploadModule {
  return {
    isNative: true,
    startUpload: (request) => module.startUpload(request),
    cancelUpload: (uploadId) => module.cancelUpload(uploadId),
    getUpload: (uploadId) => module.getUpload(uploadId),
    getUploads: () => module.getUploads(),
    addUploadListener: (listener) => module.addListener(UPLOAD_EVENT, listener),
  };
}

export const backgroundUpload: BackgroundUploadModule = nativeModule
  ? wrapNative(nativeModule)
  : createMockBackgroundUpload();

export * from "./types";
export { createMockBackgroundUpload } from "./mock";
export type { MockBackgroundUpload, MockUploadOptions } from "./mock";
