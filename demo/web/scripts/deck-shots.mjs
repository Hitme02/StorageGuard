// Screenshot every screen of the minimal deck at each reveal: node scripts/deck-shots.mjs <outdir> [base-url] [ids...]
import { chromium } from 'playwright-core'
import { mkdirSync } from 'node:fs'

const out = process.argv[2] || 'shots'
const base = process.argv[3] || 'http://127.0.0.1:8765'
const only = process.argv.slice(4)
mkdirSync(out, { recursive: true })
const SHOTS = [
  ['start', [0]], ['ticket', [0, 1, 2, 3]], ['linked', [0, 3, 4]], ['checks', [0, 1, 2, 3]], ['copy', [0, 1, 2]], ['decide', [0, 1, 2]],
  ['tickets', [0, 1]], ['play', [0]], ['hundred', [0, 1, 2]], ['versus', [0, 1, 2]], ['scale', [0, 1]], ['zfs', [0, 1]], ['real', [0, 1, 2]], ['end', [3]],
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
    await page.waitForTimeout(id === 'play' ? 9000 : id === 'zfs' ? 11000 : 4200)
    await page.screenshot({ path: `${out}/${id}_${b}.png` })
  }
}
await browser.close()
console.log(errors.length ? `ERRORS:\n${[...new Set(errors)].join('\n')}` : 'no page errors')
