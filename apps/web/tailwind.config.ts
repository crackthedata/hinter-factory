import type { Config } from "tailwindcss";

export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { 950: "#0b1220", 900: "#111827", 700: "#374151", 500: "#6b7280", 200: "#e5e7eb" },
        accent: { 600: "#2563eb", 500: "#3b82f6" },
      },
    },
  },
  plugins: [],
} satisfies Config;
