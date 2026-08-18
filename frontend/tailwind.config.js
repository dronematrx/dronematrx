/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        cyber: {
          bg: "#04060f",
          panel: "rgba(8, 14, 36, 0.6)",
          panelBorder: "rgba(0, 240, 255, 0.15)",
          blue: "#00f0ff",
          blueDim: "rgba(0, 240, 255, 0.1)",
          green: "#39ff14",
          greenDim: "rgba(57, 255, 20, 0.1)",
          red: "#ff073a",
          redDim: "rgba(255, 7, 58, 0.15)",
          orange: "#ffaa00",
          orangeDim: "rgba(255, 170, 0, 0.1)",
          gray: "#8f9bb3",
          darkGray: "#0e1329",
          grid: "rgba(0, 240, 255, 0.05)",
        }
      },
      fontFamily: {
        mono: ['"Space Mono"', 'Courier New', 'monospace'],
        sans: ['Orbitron', 'Inter', 'system-ui', 'sans-serif'],
      },
      animation: {
        'radar-sweep': 'radarSweep 6s linear infinite',
        'pulse-fast': 'pulse 1.2s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'scanline': 'scanline 10s linear infinite',
        'blink-cyber': 'blink 1.5s step-end infinite',
        'glitch': 'glitch 2s linear infinite',
      },
      keyframes: {
        radarSweep: {
          '0%': { transform: 'rotate(0deg)' },
          '100%': { transform: 'rotate(360deg)' },
        },
        scanline: {
          '0%': { transform: 'translateY(-100%)' },
          '100%': { transform: 'translateY(100%)' },
        },
        blink: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0' },
        },
        glitch: {
          '0%, 100%': { transform: 'translate(0)' },
          '20%': { transform: 'translate(-2px, 2px)' },
          '40%': { transform: 'translate(-2px, -2px)' },
          '60%': { transform: 'translate(2px, 2px)' },
          '80%': { transform: 'translate(2px, -2px)' },
        }
      },
      boxShadow: {
        'cyber-blue': '0 0 12px rgba(0, 240, 255, 0.4), inset 0 0 4px rgba(0, 240, 255, 0.1)',
        'cyber-green': '0 0 12px rgba(57, 255, 20, 0.4)',
        'cyber-red': '0 0 12px rgba(255, 7, 58, 0.4)',
        'cyber-orange': '0 0 12px rgba(255, 170, 0, 0.4)',
      }
    },
  },
  plugins: [],
}

