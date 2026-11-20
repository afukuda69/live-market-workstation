// Browser tests run against the SIMULATED mock feed so they work offline and
// when markets are closed. Live Yahoo behaviour is shown in the demo video.
import { defineConfig } from '@playwright/test'

const python = process.env.PYTHON || 'python3'

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  use: {
    baseURL: 'http://localhost:5173',
    screenshot: 'only-on-failure',
    launchOptions: process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {},
  },
  webServer: [
    {
      command: `${python} -m uvicorn app.main:app --port 8000`,
      cwd: '../backend',
      // 2-second candles and short SMAs so indicators and crossovers appear within seconds
      env: { FEED_MODE: 'mock', DB_PATH: 'data/e2e.db', BACKFILL_HISTORY: '0', FINALIZE_GRACE_SECONDS: '1',
             INTERVAL_SECONDS: '2', SMA_FAST: '2', SMA_SLOW: '4' },
      url: 'http://127.0.0.1:8000/api/health',
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
    },
    {
      command: 'npx vite --port 5173 --strictPort',
      url: 'http://localhost:5173',
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
    },
  ],
})
