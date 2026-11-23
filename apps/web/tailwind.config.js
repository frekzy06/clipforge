/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./src/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          bg: '#0f1117',
          sidebar: '#161922',
          card: '#1e2230',
          border: '#2a2f42',
          primary: '#6366f1',
          accent: '#818cf8',
        }
      }
    },
  },
  plugins: [],
}
