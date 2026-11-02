import type { Metadata } from "next";
import { Instrument_Serif, Inter_Tight } from "next/font/google";
import { MotionRoot } from "@/components/MotionRoot";
import { content } from "@/content";
import "./globals.css";

// next/font downloads at build time and serves the files from this site (no third-party requests).
const serif = Instrument_Serif({ subsets: ["latin"], weight: "400", style: ["normal", "italic"], variable: "--font-serif" });
const grotesk = Inter_Tight({ subsets: ["latin"], variable: "--font-grotesk" });

export const metadata: Metadata = {
  title: content.name,
  description: content.description,
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${serif.variable} ${grotesk.variable}`}>
      <body>
        {children}
        <MotionRoot />
      </body>
    </html>
  );
}
