import daisyui from 'daisyui'

/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,js}'],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          'Inter',
          'ui-sans-serif',
          'system-ui',
          '-apple-system',
          'BlinkMacSystemFont',
          'Segoe UI',
          'sans-serif',
        ],
      },
    },
  },
  daisyui: {
    themes: [
      {
        agentLight: {
          primary: '#0d0d0d',
          secondary: '#10a37f',
          accent: '#8b5cf6',
          neutral: '#111827',
          'base-100': '#ffffff',
          'base-200': '#f7f7f8',
          'base-300': '#ececec',
          info: '#2563eb',
          success: '#16a34a',
          warning: '#d97706',
          error: '#dc2626',
        },
      },
    ],
  },
  plugins: [daisyui],
}
