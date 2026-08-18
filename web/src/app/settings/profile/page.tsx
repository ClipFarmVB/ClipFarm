import { notFound } from "next/navigation";

import { SOCIAL_ENABLED } from "@/lib/features";

import { ProfileSettingsForm } from "./ProfileSettingsForm";

/** 404s before rendering anything when social is off — see ProfileSettingsForm. */
export default function ProfileSettingsPage() {
  if (!SOCIAL_ENABLED) notFound();
  return <ProfileSettingsForm />;
}
