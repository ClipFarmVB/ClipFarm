import type { Metadata, Viewport } from "next";
import "./globals.css";
import { ClaimHandleBanner } from "@/components/ClaimHandleBanner";
import { Sidebar } from "@/components/Sidebar";
import { AuthProvider } from "@/contexts/AuthContext";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { SOCIAL_ENABLED } from "@/lib/features";

export const metadata: Metadata = {
  title: "ClipFarm — Volleyball Highlights",
  description: "Automatically clip and organize volleyball highlights from game footage.",
};

// Explicit rather than relying on Next's default: the drawer and the clip
// player are sized against the visual viewport, so a zoomed-out initial scale
// would render the whole app at the wrong size on a phone.
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // suppressHydrationWarning: the anti-flash script mutates className before
    // React hydrates, so SSR and client class lists intentionally differ.
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Anti-flash: apply theme class synchronously before first paint */}
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var t=localStorage.getItem('cf-theme');document.documentElement.classList.toggle('dark',t!=='light')}catch(e){}})()`,
          }}
        />
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:ital,opsz,wght@0,14..32,100..900;1,14..32,100..900&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="min-h-screen bg-background text-foreground antialiased">
        <ThemeProvider>
          <AuthProvider>
            <Sidebar />
            {/* pt on mobile clears the fixed top bar Sidebar renders there;
                from lg the sidebar is a permanent column again. */}
            <main className="min-h-screen pt-[52px] lg:ml-[220px] lg:pt-0">
              <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
                {/* Mounted on every page, so gating it here is also what keeps
                    /users/me from being requested at all when social is off. */}
                {SOCIAL_ENABLED && <ClaimHandleBanner />}
                {children}
              </div>
            </main>
          </AuthProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
