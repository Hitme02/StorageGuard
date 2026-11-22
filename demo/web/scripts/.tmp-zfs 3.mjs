import { chromium } from 'playwright-core'
const out = process.argv[2]
const b = await chromium.launch({ channel: 'chrome', headless: true })
const p = await b.newPage({ viewport: { width: 1600, height: 900 } })
const errs = []
p.on('pageerror', (e) => errs.push(String(e)))
p.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()) })
await p.goto('http://127.0.0.1:8765/#/zfs/1')
const t0 = Date.now()
for (const ms of [4000, 10500]) { await p.waitForTimeout(Math.max(0, ms - (Date.now() - t0))); await p.screenshot({ path: `${out}/y1_${ms}.png` }) }
for (const name of ['Three idle volumes', 'Everything tagged temp']) {
  await p.getByRole('radio', { name }).click(); await p.waitForTimeout(13000)
  await p.screenshot({ path: `${out}/y_${name.replace(/\W+/g, '_')}.png` })
}
await p.getByRole('radio', { name: 'Qwen2.5 7B' }).click()
await p.locator('select.z-method').first().selectOption('llm_judge')
await p.waitForTimeout(16000)
await p.screenshot({ path: `${out}/y_qwen_judge.png` })
await b.close()
console.log(errs.length ? errs.join('\n') : 'no page errors')
