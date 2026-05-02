/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  corePlugins: {
    // Mevcut panel stilleri (globals) ile cakismasini onlemek icin
    preflight: false,
  },
  theme: {
    extend: {},
  },
  plugins: [],
}
