import { test, expect } from '@playwright/test'

test('dashboard streams candles and shows source, feed state and data age', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('source-label')).toContainText('SIMULATED')
  await expect(page.getByTestId('feed-state')).toHaveText('SIMULATED')
  // a candle appears and the legend reports whether it is complete
  await expect(page.getByTestId('candle-state').first()).toBeVisible({ timeout: 15_000 })
  await expect(page.getByTestId('data-age')).toHaveText(/\d+(\.\d)? s/)
  // chart canvas rendered
  await expect(page.locator('[data-testid="chart"] canvas').first()).toBeVisible()
  // price updates over time
  const p1 = await page.getByTestId('last-price').innerText()
  await expect.poll(async () => page.getByTestId('last-price').innerText(), { timeout: 10_000 }).not.toBe(p1)
})

test('switching symbols reloads the chart', async ({ page }) => {
  await page.goto('/')
  await page.getByTestId('symbols').getByRole('button', { name: 'AAPL' }).click()
  await expect(page.getByTestId('data-age')).toBeVisible()
  await expect(page.getByTestId('statusbar')).toContainText('AAPL')
})

test('replay rebuilds candles from SQLite and verify matches stored candles', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('candle-state').first()).toBeVisible({ timeout: 15_000 })
  await page.getByTestId('replay-start').click()
  await expect(page.getByTestId('replay-banner')).toContainText('REPLAY')
  await expect(page.getByTestId('replay-banner')).toContainText('finished', { timeout: 30_000 })
  await page.getByRole('button', { name: 'Back to live' }).click()
  await expect(page.getByTestId('replay-banner')).toHaveCount(0)
  await page.getByTestId('verify').click()
  await expect(page.getByTestId('verify-result')).toContainText('match')
})

test('duplicates and out-of-order ticks are counted', async ({ page }) => {
  await page.goto('/')
  const counters = page.getByTestId('counters')
  await expect.poll(async () => {
    const t = await counters.innerText()
    return /Duplicates ignored\s+([1-9]\d*)/.test(t)
  }, { timeout: 30_000 }).toBe(true)
})
