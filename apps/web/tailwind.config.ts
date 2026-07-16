import type { Config } from "tailwindcss";
import tailwindcssAnimate from "tailwindcss-animate";

const c = (v: string) => `hsl(var(${v}) / <alpha-value>)`;

const config: Config = {
  darkMode: "class",
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // shadcn 标准
        background: c("--background"),
        foreground: c("--foreground"),
        card: { DEFAULT: c("--card"), foreground: c("--card-foreground") },
        popover: { DEFAULT: c("--popover"), foreground: c("--popover-foreground") },
        primary: { DEFAULT: c("--primary"), foreground: c("--primary-foreground") },
        secondary: { DEFAULT: c("--secondary"), foreground: c("--secondary-foreground") },
        muted: { DEFAULT: c("--muted"), foreground: c("--muted-foreground") },
        accent: { DEFAULT: c("--accent"), foreground: c("--accent-foreground") },
        destructive: { DEFAULT: c("--destructive"), foreground: c("--destructive-foreground") },
        border: c("--border"),
        input: c("--input"),
        ring: c("--ring"),

        // shadcn sidebar 组件令牌
        sidebar: {
          DEFAULT: "hsl(var(--sidebar-background))",
          foreground: "hsl(var(--sidebar-foreground))",
          primary: "hsl(var(--sidebar-primary))",
          "primary-foreground": "hsl(var(--sidebar-primary-foreground))",
          accent: "hsl(var(--sidebar-accent))",
          "accent-foreground": "hsl(var(--sidebar-accent-foreground))",
          border: "hsl(var(--sidebar-border))",
          ring: "hsl(var(--sidebar-ring))",
        },
        chart: {
          1: "hsl(var(--chart-1))",
          2: "hsl(var(--chart-2))",
          3: "hsl(var(--chart-3))",
          4: "hsl(var(--chart-4))",
          5: "hsl(var(--chart-5))",
        },

        // 状态色（成功态；danger 用标准 destructive）
        success: c("--success"),
      },
      fontFamily: {
        sans: ["var(--font-sans)"],
        mono: ["var(--font-mono)"],
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      boxShadow: {
        // 极轻，边框优先（Notion/Codex）
        soft: "0 2px 8px -4px rgb(24 36 58 / 0.16), 0 1px 2px rgb(24 36 58 / 0.05)",
        lift: "0 16px 38px -16px rgb(24 36 58 / 0.28), 0 4px 10px -5px rgb(224 90 32 / 0.14)",
      },
      transitionTimingFunction: {
        smooth: "cubic-bezier(0.2, 0, 0, 1)",
      },
      keyframes: {
        "fade-in": {
          from: { opacity: "0", transform: "translateY(4px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "accordion-down": {
          from: { height: "0" },
          to: { height: "var(--radix-accordion-content-height)" },
        },
        shimmer: {
          "100%": { transform: "translateX(100%)" },
        },
        blink: { "50%": { opacity: "0.2" } },
      },
      animation: {
        "fade-in": "fade-in 0.24s cubic-bezier(0.2,0,0,1)",
        shimmer: "shimmer 1.6s infinite",
        blink: "blink 1s step-start infinite",
      },
    },
  },
  plugins: [tailwindcssAnimate],
};

export default config;
