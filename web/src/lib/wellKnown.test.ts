import { describe, expect, it } from "vitest";
import {
  APPLE_COMPONENTS,
  buildAppleAssociation,
  buildAssetLinks,
  parseFingerprints,
} from "./wellKnown";

/** 32 colon-separated hex bytes, built rather than typed so it is obviously the right length. */
const fingerprint = (start: number) =>
  Array.from({ length: 32 }, (_, i) => ((start + i) % 256).toString(16).padStart(2, "0"))
    .join(":")
    .toUpperCase();

const FP_A = fingerprint(0);
const FP_B = fingerprint(64);

const IOS = { IOS_APP_ID: "ABCDE12345.app.clipfarm.mobile" };
const ANDROID = {
  ANDROID_PACKAGE_NAME: "app.clipfarm.mobile",
  ANDROID_SHA256_CERT_FINGERPRINTS: FP_A,
};

describe("parseFingerprints", () => {
  it("accepts a single fingerprint", () => {
    expect(parseFingerprints(FP_A)).toEqual([FP_A]);
  });

  it("accepts a comma-separated list, trimming around the separators", () => {
    expect(parseFingerprints(` ${FP_A} , ${FP_B} `)).toEqual([FP_A, FP_B]);
  });

  it("normalises to upper case, so a fingerprint pasted from either tool matches", () => {
    expect(parseFingerprints(FP_A.toLowerCase())).toEqual([FP_A]);
  });

  it.each([undefined, "", "   ", ",", " , "])("returns null for %j", (raw) => {
    expect(parseFingerprints(raw)).toBeNull();
  });

  // The whole list is rejected, not just the bad entry. Dropping one silently
  // presents as "app links work from my local build and not from the Play
  // build" — the failure CF-322 names as the most common one on Android.
  describe("rejects the whole list when any entry is not a SHA-256 fingerprint", () => {
    it.each([
      FP_A.slice(0, -3), // 31 bytes
      `${FP_A}:00`, // 33 bytes
      FP_A.replace(":", ""), // a missing separator
      FP_A.replace("0", "Z"), // not hex
      FP_A.replace(/:/g, " "), // space-separated
      "not-a-fingerprint",
    ])("%j", (bad) => {
      expect(parseFingerprints(bad)).toBeNull();
    });
  });

  it("rejects a list where only one of several entries is malformed", () => {
    expect(parseFingerprints(`${FP_A},not-a-fingerprint,${FP_B}`)).toBeNull();
  });
});

describe("APPLE_COMPONENTS", () => {
  // Apple stops at the first matching component, so an exclude listed after the
  // include it carves out of does nothing at all — and does nothing *silently*.
  it("lists every exclusion before the first claimed path", () => {
    const firstClaimed = APPLE_COMPONENTS.findIndex((c) => !c.exclude);
    const lastExcluded = APPLE_COMPONENTS.map((c) => !!c.exclude).lastIndexOf(true);
    expect(firstClaimed).toBeGreaterThan(-1);
    expect(lastExcluded).toBeLessThan(firstClaimed);
  });

  it("excludes the routes the web app has to keep", () => {
    const excluded = APPLE_COMPONENTS.filter((c) => c.exclude).map((c) => c["/"]);
    expect(excluded).toContain("/auth/*");
    expect(excluded).toContain("/login");
    expect(excluded).toContain("/signup");
  });

  it("claims an allowlist rather than the whole domain", () => {
    const claimed = APPLE_COMPONENTS.filter((c) => !c.exclude).map((c) => c["/"]);
    expect(claimed.length).toBeGreaterThan(0);
    expect(claimed).not.toContain("/*");
    expect(claimed).not.toContain("*");
  });
});

describe("buildAppleAssociation", () => {
  it("builds the iOS 13+ appIDs/components form", () => {
    expect(buildAppleAssociation(IOS)).toEqual({
      applinks: {
        apps: [],
        details: [
          { appIDs: ["ABCDE12345.app.clipfarm.mobile"], components: APPLE_COMPONENTS },
        ],
      },
    });
  });

  it("trims the configured app ID", () => {
    const built = buildAppleAssociation({ IOS_APP_ID: "  ABCDE12345.app.clipfarm.mobile  " });
    expect(built?.applinks.details[0].appIDs).toEqual(["ABCDE12345.app.clipfarm.mobile"]);
  });

  it.each([{}, { IOS_APP_ID: "" }, { IOS_APP_ID: "   " }])(
    "returns null rather than a placeholder document for %j",
    (env) => {
      expect(buildAppleAssociation(env)).toBeNull();
    }
  );
});

describe("buildAssetLinks", () => {
  it("builds the single-target document Google expects", () => {
    expect(buildAssetLinks(ANDROID)).toEqual([
      {
        relation: ["delegate_permission/common.handle_all_urls"],
        target: {
          namespace: "android_app",
          package_name: "app.clipfarm.mobile",
          sha256_cert_fingerprints: [FP_A],
        },
      },
    ]);
  });

  it("carries every configured fingerprint, so an upload key can sit beside the Play key", () => {
    const built = buildAssetLinks({
      ...ANDROID,
      ANDROID_SHA256_CERT_FINGERPRINTS: `${FP_A},${FP_B}`,
    });
    expect(built?.[0].target.sha256_cert_fingerprints).toEqual([FP_A, FP_B]);
  });

  it("trims the configured package name", () => {
    const built = buildAssetLinks({ ...ANDROID, ANDROID_PACKAGE_NAME: " app.clipfarm.mobile " });
    expect(built?.[0].target.package_name).toBe("app.clipfarm.mobile");
  });

  it.each([
    { ...ANDROID, ANDROID_PACKAGE_NAME: undefined },
    { ...ANDROID, ANDROID_PACKAGE_NAME: "  " },
    { ...ANDROID, ANDROID_SHA256_CERT_FINGERPRINTS: undefined },
    { ...ANDROID, ANDROID_SHA256_CERT_FINGERPRINTS: "nonsense" },
    {},
  ])("returns null when the configuration is incomplete or malformed", (env) => {
    expect(buildAssetLinks(env)).toBeNull();
  });
});
