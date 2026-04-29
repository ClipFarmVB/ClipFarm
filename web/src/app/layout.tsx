import type { Metadata } from "next";
import "./globals.css";
import { Sidebar } from "@/components/Sidebar";
import { AuthProvider } from "@/contexts/AuthContext";
import { ThemeProvider } from "@/contexts/ThemeContext";

export const metadata: Metadata = {
  title: "ClipFarm — Volleyball Highlights",
  description: "Automatically clip and organize volleyball highlights from game footage.",
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
            <main className="ml-[220px] min-h-screen">
              <div className="mx-auto max-w-5xl px-8 py-8">
                {children}
              </div>
            </main>
          </AuthProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
