import { test, expect } from '@playwright/test'

test('SMA lines appear and live incremental SMA matches the batch recomputation', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('sma-fast')).toContainText(/SMA 2 [\d,]+\.\d+/, { timeout: 30_000 })
  await expect(page.getByTestId('sma-slow')).toContainText(/SMA 4 [\d,]+\.\d+/, { timeout: 30_000 })
  await page.getByTestId('verify-sma').click()
  await expect(page.getByTestId('verify-sma-result')).toContainText('match')
})

test('simulated account panel shows equity, cash and signals', async ({ page }) => {
  await page.goto('/')
  const acct = page.getByTestId('account')
  await expect(acct).toContainText('SIMULATED')
  await expect(page.getByTestId('account-equity')).toContainText(/\d/)
  await expect(page.getByTestId('account-cash')).toContainText(/\d/)
  // with 2 s candles and SMA 2/4 the random walk crosses quickly
  await expect(page.getByTestId('signals')).toContainText(/up|down/, { timeout: 45_000 })
})

test('backtest on the real SPY fixture shows trades, metrics and equity', async ({ page }) => {
  await page.goto('/')
  await page.getByTestId('tab-backtest').click()
  await page.getByTestId('bt-run').click()
  await expect(page.getByTestId('bt-metrics')).toContainText('390 bars')
  await expect(page.getByTestId('bt-trips')).toHaveText(/^[1-9]\d*$/)
  await expect(page.getByTestId('bt-trades')).toContainText('%')
  await expect(page.locator('[data-testid="bt-equity"] canvas').first()).toBeVisible()
  await expect(page.getByTestId('bt-notes')).toContainText('No look-ahead')
  await expect(page.getByTestId('bt-runs')).toContainText('fixture')
})

test('backtest explains when there is not enough data', async ({ page }) => {
  await page.goto('/')
  await page.getByTestId('tab-backtest').click()
  await page.getByTestId('bt-source').selectOption('stored')
  await page.getByTestId('bt-slow').fill('400')
  await page.getByTestId('bt-run').click()
  await expect(page.getByTestId('bt-error')).toContainText('completed bars')
})
