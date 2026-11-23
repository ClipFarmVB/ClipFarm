import { afterEach, describe, expect, it, vi } from "vitest";
import { GET as getAppleAssociation } from "../app/.well-known/apple-app-site-association/route";
import { GET as getAssetLinks } from "../app/.well-known/assetlinks.json/route";

/**
 * The two route handlers, exercised directly. They live under a dot-directory,
 * which TypeScript's and vitest's globs both skip, so this file sits beside the
 * library and imports them by path — otherwise nothing but `next build` reaches
 * them, and CI does not run a build.
 */

const FP = Array.from({ length: 32 }, () => "AB").join(":");

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("GET /.well-known/apple-app-site-association", () => {
  it("serves the document as application/json when configured", async () => {
    vi.stubEnv("IOS_APP_ID", "ABCDE12345.app.clipfarm.mobile");
    const res = await getAppleAssociation();
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("application/json");
    const body = await res.json();
    expect(body.applinks.details[0].appIDs).toEqual(["ABCDE12345.app.clipfarm.mobile"]);
  });

  it("404s when unconfigured", async () => {
    vi.stubEnv("IOS_APP_ID", "");
    const res = await getAppleAssociation();
    expect(res.status).toBe(404);
  });
});

describe("GET /.well-known/assetlinks.json", () => {
  it("serves the document as application/json when configured", async () => {
    vi.stubEnv("ANDROID_PACKAGE_NAME", "app.clipfarm.mobile");
    vi.stubEnv("ANDROID_SHA256_CERT_FINGERPRINTS", FP);
    const res = await getAssetLinks();
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("application/json");
    const body = await res.json();
    expect(body[0].target.sha256_cert_fingerprints).toEqual([FP]);
  });

  it("404s when unconfigured", async () => {
    vi.stubEnv("ANDROID_PACKAGE_NAME", "");
    vi.stubEnv("ANDROID_SHA256_CERT_FINGERPRINTS", "");
    const res = await getAssetLinks();
    expect(res.status).toBe(404);
  });
});
