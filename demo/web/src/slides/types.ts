import type { ComponentType } from 'react'

export interface SlideProps {
  build: number
  setBuild: (b: number) => void
  go: (id: string) => void
}

export interface SlideDef {
  id: string
  chapter: number
  title: string
  /** on the 15-minute core path; detours are skipped while the core path is on */
  core: boolean
  /** how many presses of "next" the screen takes (reveals) */
  builds: number
  C: ComponentType<SlideProps>
}
