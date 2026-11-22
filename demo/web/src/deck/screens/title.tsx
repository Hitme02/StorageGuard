// The landing screen: the mark, the name, one line on what it is, and who made it.
// The mark is the deck's motif: a solid disc is the real storage, the dashed ring is the guard's copy of it.
import { motion } from 'framer-motion'
import type { ScreenProps } from '../ui'

const TEAM = 'Hitarth Mehra and Sumadhva Krishna'
const GUIDE = 'Dr. Vinay Hegde'
const PLACE = 'Department of CSE, RV College of Engineering, Bengaluru'

// Same proportions as the small mark in the top bar: radius 8, centres 10 apart.
const R = 66
const OFF = R * 1.25
const PAD = 8
const W = PAD + R + OFF + R + PAD
const H = PAD + R * 2 + PAD
const CX = PAD + R
const CY = PAD + R

const ease = [0.2, 0.7, 0.2, 1] as const

export function Title(_: ScreenProps) {
  return (
    <div className="d-screen">
      <div className="t-center">
        <div className="t-lockup">
          <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} aria-hidden style={{ overflow: 'visible' }}>
            <motion.circle cx={CX} cy={CY} r={R} fill="#eeeaf4"
              style={{ transformBox: 'fill-box', transformOrigin: 'center' }}
              initial={{ scale: 0.5, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}
              transition={{ type: 'spring', stiffness: 90, damping: 13, delay: 0.15 }} />
            {/* the copy peels off the real thing */}
            <motion.g initial={{ x: -OFF, opacity: 0 }} animate={{ x: 0, opacity: 1 }}
              transition={{ x: { type: 'spring', stiffness: 55, damping: 13, delay: 0.75 }, opacity: { duration: 0.3, delay: 0.6 } }}>
              <circle className="t-ring" cx={CX + OFF} cy={CY} r={R} fill="none" stroke="#e052c8" strokeWidth={11}
                pathLength={240} strokeDasharray="6 4" />
            </motion.g>
          </svg>
          <h1 className="t-word">
            <motion.span initial={{ x: '-35%', opacity: 0 }} animate={{ x: 0, opacity: 1 }} transition={{ duration: 1, ease, delay: 1.15 }}>
              StorageGuard
            </motion.span>
          </h1>
        </div>
        <motion.p className="t-line" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.7, ease, delay: 1.7 }}>
          A safety layer for AI agents that manage storage.
        </motion.p>
        <motion.p className="t-sub" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.7, ease, delay: 1.95 }}>
          And StorageAgentBench, the test bed we built to measure it.
        </motion.p>
      </div>
      <motion.div className="t-credits" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.8, delay: 2.4 }}>
        <div className="who">{TEAM}</div>
        <div>Guided by {GUIDE}</div>
        <div>{PLACE}</div>
      </motion.div>
    </div>
  )
}
