import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        forest: "#15372d",
        lime: "#c7f27c",
        canvas: "#f4f6f2",
      },
      boxShadow: {
        panel: "0 12px 34px rgba(22, 47, 35, .07)",
      },
    },
  },
  plugins: [],
};

export default config;
