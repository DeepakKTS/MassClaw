import type { Metadata } from "next";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import "./globals.css";
import { TopBar } from "@/components/layout/TopBar";
import { BottomDock } from "@/components/layout/BottomDock";
import { CommandPalette } from "@/components/layout/CommandPalette";
import { CursorGrid } from "@/components/effects/CursorGrid";
import { Providers } from "@/components/Providers";

export const metadata: Metadata = {
  title: "MassClaw — Mission Control",
  description: "Decentralized Operating Layer for AI Agents",
  icons: { icon: "/favicon.ico" },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`dark ${GeistSans.variable} ${GeistMono.variable}`}>
      <body className="bg-massclaw-bg text-massclaw-text font-sans antialiased">
        <Providers>
          <CursorGrid />
          <div className="relative min-h-screen flex flex-col">
            <TopBar />
            <main className="flex-1 overflow-auto pt-16 pb-24 px-5 sm:px-8 relative z-10">
              {children}
            </main>
            <BottomDock />
            <CommandPalette />
          </div>
        </Providers>
      </body>
    </html>
  );
}
