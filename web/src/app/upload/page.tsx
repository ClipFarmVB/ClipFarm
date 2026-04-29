"use client";

import { UploadZone } from "@/components/UploadZone";
import { RequireAuth } from "@/components/RequireAuth";

export default function UploadPage() {
  return (
    <RequireAuth>
      <div className="fade-up">
        <div className="mb-7">
          <h1 className="text-[18px] font-semibold text-foreground tracking-tight">Upload a game</h1>
          <p className="mt-0.5 text-[12px] text-muted">
            Drop your footage and we&apos;ll generate a highlight reel automatically.
          </p>
        </div>
        <UploadZone />
      </div>
    </RequireAuth>
  );
}
