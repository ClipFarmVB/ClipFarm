// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const replace = vi.fn();
let auth: { user: { id: string } | null; loading: boolean } = { user: null, loading: true };

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => auth }));

import { SignedInRedirect } from "./SignedInRedirect";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  replace.mockReset();
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("SignedInRedirect", () => {
  it("sends a signed-in user to /games", () => {
    auth = { user: { id: "u1" }, loading: false };
    act(() => root.render(<SignedInRedirect />));

    expect(replace).toHaveBeenCalledWith("/games");
  });

  it("does nothing while the session is still loading", () => {
    auth = { user: null, loading: true };
    act(() => root.render(<SignedInRedirect />));

    expect(replace).not.toHaveBeenCalled();
  });

  it("does nothing for a signed-out visitor", () => {
    auth = { user: null, loading: false };
    act(() => root.render(<SignedInRedirect />));

    expect(replace).not.toHaveBeenCalled();
    expect(host.innerHTML).toBe("");
  });
});
