import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Hinter Factory",
  description: "Weak supervision workspace",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        <header className="border-b border-ink-900 bg-ink-900/40">
          <div className="mx-auto flex max-w-6xl items-center justify-between gap-6 px-4 py-4">
            <div className="text-sm font-semibold tracking-tight text-white">Hinter Factory</div>
            <nav className="flex items-center gap-4 text-sm text-ink-200">
              <Link href="/explore">Explore</Link>
              <Link href="/studio">LF Studio</Link>
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-4 py-8">{children}</main>
      </body>
    </html>
  );
}
