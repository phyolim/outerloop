/** @type {import('tailwindcss').Config} */
// Mission Control palette — tokens from docs/design_handoff_mission_control/README.md
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: '#0d0f13', // page background
        panel: '#171a20', // cards, panels
        deep: '#101318', // log tables, terminal
        well: '#12151b', // inputs
        tx: '#e8eaf0', // primary text
        tx2: '#9aa2b1', // secondary
        tx3: '#5d6470', // muted labels
        acc: '#3ddc84', // phosphor green
        warn: '#f5b843',
        bad: '#f26d6d',
        info: '#5eb1f7',
        proj: '#a78bfa',
        hairline: 'rgba(255,255,255,0.07)',
        hairline2: 'rgba(255,255,255,0.05)',
      },
    },
  },
  plugins: [],
}
