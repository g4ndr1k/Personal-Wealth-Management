/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        surface: {
          DEFAULT: "#1a1b1e",
          card: "#25262b",
          hover: "#2c2d32",
          border: "#373a40",
        },
        accent: {
          blue: "#4c6ef5",
          green: "#40c057",
          yellow: "#fab005",
          red: "#fa5252",
          purple: "#7950f2",
        },
      },
      fontFamily: {
        sans: [
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
        mono: ["SF Mono", "Fira Code", "Fira Mono", "monospace"],
      },
    },
  },
  plugins: [],
};
