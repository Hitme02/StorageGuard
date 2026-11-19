// Screenshot every screen (and its reveals) for review: node scripts/shots.mjs <outdir> [base-url] [ids...]
import { chromium } from 'playwright-core'
import { mkdirSync } from 'node:fs'

const out = process.argv[2] || 'shots'
const base = process.argv[3] || 'http://127.0.0.1:8765'
const only = process.argv.slice(4)
mkdirSync(out, { recursive: true })
const SHOTS = [
  ['1.1', [0, 1, 2, 3]], ['1.2', [5]], ['1.3', [0, 3, 6]], ['1.4', [2]], ['1.5', [0, 1]], ['1.6', [4]],
  ['2.1', [2]], ['2.2', [0]], ['2.3', [0]], ['3.1', [2]], ['3.2', [0, 1, 2, 3, 4, 5]], ['3.3', [0]],
  ['4.1', [0]], ['4.2', [0]], ['5.1', [2]], ['5.2', [0]], ['5.3', [0]], ['6.1', [2]], ['6.2', [0]], ['7', [3]],
]
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const page = await browser.newPage({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1 })
const errors = []
page.on('pageerror', (e) => errors.push(String(e)))
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
for (const [id, builds] of SHOTS) {
  if (only.length && !only.includes(id)) continue
  for (const b of builds) {
    await page.goto('about:blank')
    await page.goto(`${base}/#/${id}/${b}`)
    await page.waitForTimeout(id.startsWith('4') ? 4500 : 2600)
    await page.screenshot({ path: `${out}/${id}_${b}.png` })
  }
}
await browser.close()
console.log(errors.length ? `ERRORS:\n${[...new Set(errors)].join('\n')}` : 'no page errors')
