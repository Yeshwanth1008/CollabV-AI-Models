import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class"],
  content: [
    "./src/pages/**/*.{ts,tsx}",
    "./src/components/**/*.{ts,tsx}",
    "./src/app/**/*.{ts,tsx}",
  ],
  theme: {
    container: { center: true, padding: "2rem", screens: { "2xl": "1400px" } },
    extend: {
      colors: {
        border: "hsl(220 13% 18%)",
        background: "hsl(222 47% 6%)",
        foreground: "hsl(210 40% 98%)",
        muted: { DEFAULT: "hsl(217 33% 17%)", foreground: "hsl(215 20% 65%)" },
        primary: { DEFAULT: "hsl(217 91% 60%)", foreground: "hsl(0 0% 100%)" },
        accent: { DEFAULT: "hsl(160 84% 39%)", foreground: "hsl(0 0% 100%)" },
        destructive: { DEFAULT: "hsl(0 84% 60%)", foreground: "hsl(0 0% 100%)" },
        card: { DEFAULT: "hsl(222 47% 9%)", foreground: "hsl(210 40% 98%)" },
        warning: "hsl(38 92% 50%)",
        success: "hsl(160 84% 39%)",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "Menlo", "monospace"],
      },
      borderRadius: { lg: "0.5rem", md: "0.375rem", sm: "0.25rem" },
    },
  },
  plugins: [require("tailwindcss-animate")],
};
export default config;
