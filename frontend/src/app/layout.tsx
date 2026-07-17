import "./globals.css";
import type { Metadata, Viewport } from "next";
import { Providers } from "./providers";
import { TopNav } from "@/components/top-nav";

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || "https://collabv.ai";
const TITLE = "CollabV AI — Match innovation with India's top academic minds";
const DESC =
  "B2B platform matching companies with IIT Madras professors for R&D collaboration. " +
  "543 professors, 16 departments, ranked by research alignment, patent portfolio, " +
  "readiness, and deal success probability.";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: { default: TITLE, template: "%s | CollabV AI" },
  description: DESC,
  applicationName: "CollabV AI",
  authors: [{ name: "CollabV AI" }],
  keywords: [
    "IIT Madras",
    "professor matching",
    "industry collaboration",
    "R&D",
    "academic research",
    "B2B platform",
  ],
  openGraph: {
    type: "website",
    url: SITE_URL,
    title: TITLE,
    description: DESC,
    siteName: "CollabV AI",
    images: [{ url: "/og-image.png", width: 1200, height: 630, alt: "CollabV AI" }],
  },
  twitter: {
    card: "summary_large_image",
    title: TITLE,
    description: DESC,
    images: ["/og-image.png"],
  },
  icons: {
    icon: [
      { url: "/favicon.svg", type: "image/svg+xml" },
      { url: "/favicon.ico", sizes: "any" },
    ],
    apple: "/apple-touch-icon.png",
  },
  robots: { index: true, follow: true },
};

export const viewport: Viewport = {
  themeColor: "#0F172A",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-background text-foreground antialiased">
        <Providers>
          <TopNav />
          <main className="container mx-auto py-8 px-4">{children}</main>
        </Providers>
      </body>
    </html>
  );
}
