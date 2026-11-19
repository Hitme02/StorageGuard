// Chapter 6: reality check, and the wrap-up.
import { motion } from 'framer-motion'
import type { ReactNode } from 'react'
import { HBars } from '../components/charts'
import { Eng, Loading, SlideHead, Term } from '../components/ui'
import { fetchResults, useLoad } from '../lib/api'
import { CONFIGS, REPORT_CONFIG } from '../lib/plain'
import type { SlideProps } from './types'

const reveal = (on: boolean, delay = 0) => ({
  initial: false as const,
  animate: { opacity: on ? 1 : 0, y: on ? 0 : 10 },
  transition: { duration: 0.4, delay: on ? delay : 0 },
})

const Big = ({ children, color }: { children: ReactNode; color?: string }) => (
  <div style={{ fontFamily: 'var(--cond)', fontSize: 76, fontWeight: 600, lineHeight: 1, color }}>{children}</div>
)

// ---------------------------------------------------------------- 6.1 did it really happen?
export function Really({ build }: SlideProps) {
  const { data: res } = useLoad(fetchResults, [])
  if (!res) return <Loading />
  const z = res.zfs
  const phys = res.physical
  const b0 = phys.find((r: any) => r.config === 'B0 No guard')
  const b4 = phys.find((r: any) => r.config === 'B4 StorageGuard')
  return (
    <>
      <SlideHead title="We checked the simulation against real deletions." lede="A pretend system can be wrong about what “harm” means. So every recorded run was executed again where the damage is real." />
      <div className="row" style={{ gap: 18 }}>
        <motion.div className="panel col" style={{ flex: 1, gap: 8 }} {...reveal(build >= 0)}>
          <div className="small muted">On real files, in a <Term id="sandbox">sandbox</Term></div>
          <Big>866 of 866</Big>
          <div style={{ fontSize: 19 }}>times the answer key said data was lost, the files agreed, and it never claimed a loss the files did not show.</div>
          <div className="small ink2">10,920 runs replayed exactly; 8,814 files really deleted and 1,286 overwritten; about 28 minutes on a laptop.</div>
        </motion.div>
        <motion.div className="panel col" style={{ flex: 1, gap: 8 }} {...reveal(build >= 1)}>
          <div className="small muted">On a real storage system, <Term id="zfs">OpenZFS</Term>, in a throwaway virtual machine</div>
          <Big>{z.agree_pct}%</Big>
          <div style={{ fontSize: 19 }}>of {z.comparable?.toLocaleString() ?? 'the'} comparable calls behaved the same as in the simulation.</div>
          <div className="small ink2">Every delete removed exactly the same things ({z.cascade_match[0].toLocaleString()} of {z.cascade_match[1].toLocaleString()}), and the end state read back byte-identical in all {z.trajectories.toLocaleString()} distinct runs.</div>
        </motion.div>
      </div>
      <motion.div className="row grow" style={{ gap: 18 }} {...reveal(build >= 2)}>
        <div className="panel" style={{ width: 820 }}>
          <div className="panel-title">Runs that caused real harm on files (main-study agents, 936 runs each)</div>
          <HBars data={phys.map((r: any) => ({ label: CONFIGS[REPORT_CONFIG[r.config]]?.label || r.config, value: r.phys_harm, ci: r.phys_harm_ci, hl: r.config.startsWith('B4'), color: r.config.startsWith('B4') ? undefined : 'var(--ink-3)' }))} width={760} max={40} labelW={260} rowH={36} />
        </div>
        <div className="col grow" style={{ gap: 14, justifyContent: 'center' }}>
          <div><Big color="#f07b7f">{b0.tb_destroyed} TB</Big><div className="ink2" style={{ fontSize: 19 }}>of data destroyed or rolled back without a guard ({b0.datasets_destroyed} datasets)</div></div>
          <div><Big color="var(--brand-ink)">{b4.tb_destroyed} TB</Big><div className="ink2" style={{ fontSize: 19 }}>with StorageGuard, for any of the seven models</div></div>
        </div>
      </motion.div>
      <Eng>Agreement on lost data κ = 1.00 (866/866); lost access κ = 0.98; protection loss κ = 0.86 (the answer key also counts protection that the three drills cannot show). ZFS: {z.trajectories} distinct trajectories covering {z.episodes} episodes.</Eng>
    </>
  )
}

// ---------------------------------------------------------------- 6.2 what reality taught us (detour)
export function Taught() {
  const { data: res } = useLoad(fetchResults, [])
  if (!res) return <Loading />
  const pit = res.pit
  const rs = res.restore_sensitivity
  return (
    <>
      <SlideHead title="Running it for real taught us two things the simulation missed." />
      <div className="row grow" style={{ gap: 20 }}>
        <div className="panel col" style={{ flex: 1, gap: 14 }}>
          <div style={{ fontFamily: 'var(--cond)', fontSize: 30, fontWeight: 600 }}>Backups eroding one at a time</div>
          <div style={{ fontSize: 19 }}>Some agents deleted the newest snapshots of a volume one by one. Each deletion looked fine on its own, because older copies remained. Neither the guard nor the answer key counted it. The drills did: a ransomware attack would lose more recent data. We call it <Term id="erosion">recovery-point erosion</Term>.</div>
          <div className="ink2" style={{ fontSize: 18 }}>The fix asks one more question: is the newest copy of this data about to go? Added afterwards, tuned on the practice tickets only, and re-run live with the three main agents:</div>
          <div className="row" style={{ gap: 26, alignItems: 'baseline', marginTop: 10 }}>
            <div><Big>{pit.b4} → {pit.pit}</Big><div className="ink2">of {pit.of} runs with real harm</div></div>
            <div className="ink2" style={{ maxWidth: 330, fontSize: 17 }}>The 10 erosion runs were all stopped. The 9 left are a different problem: an action on the wrong object, which needs an intent check.</div>
          </div>
        </div>
        <div className="panel col" style={{ flex: 1, gap: 14 }}>
          <div style={{ fontFamily: 'var(--cond)', fontSize: 30, fontWeight: 600 }}>The pretend system’s restore was too gentle</div>
          <div style={{ fontSize: 19 }}>When ZFS, or NetApp ONTAP, rolls a volume back, it deletes the snapshots newer than the restore point. Our simulation kept them. So we re-measured every run with the real behaviour.</div>
          <table className="tbl">
            <thead><tr><th>Method</th><th>Real harm, simulated restore</th><th>With real restore</th><th>Beyond what the correct procedure loses</th></tr></thead>
            <tbody>
              {rs.map((r: any) => (
                <tr key={r.config} className={r.config.startsWith('B4') ? 'hl' : ''}>
                  <td>{CONFIGS[REPORT_CONFIG[r.config]]?.label}</td><td>{r.twin}%</td><td>{r.rollback_r}%</td><td>{r.beyond_reference}%</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="ink2" style={{ fontSize: 18, marginTop: 'auto' }}>The numbers move, but the order of the methods does not. The extra harm is what any in-place restore costs on such a system, the correct procedure included.</div>
        </div>
      </div>
    </>
  )
}

// ---------------------------------------------------------------- wrap-up
const TAKEAWAYS = [
  { big: '40–53%', color: '#f07b7f', text: 'of tickets went wrong when AI agents worked storage without a guard, at every model size from 7B to 72B. The danger was in links the ticket never mentioned.' },
  { big: '0–2%', color: 'var(--brand-ink)', text: 'with StorageGuard trying each change on a copy first, while more tickets got done, and a fifth to a third of the human approvals that “ask before any delete” needs.' },
  { big: '866/866', color: 'var(--ink)', text: 'times the answer key’s “data lost” matched real deleted files. Real runs also exposed two gaps, which we measured and reported.' },
]

export function Wrap({ build }: SlideProps) {
  return (
    <>
      <SlideHead title="Three things to remember." />
      <div className="col grow" style={{ gap: 18 }}>
        {TAKEAWAYS.map((t, i) => (
          <motion.div key={i} className="row" style={{ gap: 28, alignItems: 'baseline', borderTop: '1px solid var(--line)', paddingTop: 16 }} {...reveal(build >= i)}>
            <div style={{ fontFamily: 'var(--cond)', fontSize: 72, fontWeight: 600, lineHeight: 1, width: 300, flex: 'none', color: t.color }}>{t.big}</div>
            <div style={{ fontSize: 24, maxWidth: 980 }}>{t.text}</div>
          </motion.div>
        ))}
      </div>
      <motion.div {...reveal(build >= 3)} className="row" style={{ alignItems: 'flex-end', gap: 30 }}>
        <div style={{ fontFamily: 'var(--cond)', fontSize: 64, fontWeight: 600, lineHeight: 1 }}>Questions?</div>
        <div className="small ink2" style={{ maxWidth: 820 }}>
          Next: an intent check that matches an action’s targets to what the ticket names; safer restore procedures specific to each storage system; validation against a vendor array; evaluation with frontier models. Press <kbd>4</kbd> to open the control room.
        </div>
      </motion.div>
    </>
  )
}
