import type { Metadata } from "next";
import "./globals.css";
import { Sidebar } from "@/components/Sidebar";
import { AuthProvider } from "@/contexts/AuthContext";

export const metadata: Metadata = {
  title: "ClipFarm — Volleyball Highlights",
  description: "Automatically clip and organize volleyball highlights from game footage.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:ital,opsz,wght@0,14..32,100..900;1,14..32,100..900&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="min-h-screen bg-background text-foreground antialiased">
        <AuthProvider>
          <Sidebar />
          <main className="ml-[220px] min-h-screen">
            <div className="mx-auto max-w-5xl px-8 py-8">
              {children}
            </div>
          </main>
        </AuthProvider>
      </body>
    </html>
  );
}
