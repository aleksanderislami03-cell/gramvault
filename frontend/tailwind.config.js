/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        surface: {
          DEFAULT: '#0e0f12',
          raised: '#16181d',
          overlay: '#1e2129',
          border: '#2a2e38',
        },
        accent: {
          DEFAULT: '#7c9eff',
          muted: '#4f6bb0',
          soft: '#33395a',
        },
      },
      fontFamily: {
        sans: [
          'Inter',
          'ui-sans-serif',
          'system-ui',
          '-apple-system',
          'Segoe UI',
          'Roboto',
          'sans-serif',
        ],
      },
    },
  },
  plugins: [],
}
