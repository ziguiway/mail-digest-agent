import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  use: { baseURL: 'http://127.0.0.1:8771', trace: 'retain-on-failure' },
  webServer: {
    command: '../../.venv/bin/python ../../backend/tests/serve_browser.py',
    url: 'http://127.0.0.1:8771/api/v1/health',
    reuseExistingServer: false,
    timeout: 30_000,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
