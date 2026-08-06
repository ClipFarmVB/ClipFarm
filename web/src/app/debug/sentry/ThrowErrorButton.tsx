"use client";

// Client half of the CF-89 Sentry debug helper. Throwing inside an event
// handler is what produces an unhandled browser error for Sentry to capture.
export default function ThrowErrorButton() {
  return (
    <button
      type="button"
      onClick={() => {
        throw new Error("CF-89 test error from the web app (debug only)");
      }}
    >
      Throw test error
    </button>
  );
}
