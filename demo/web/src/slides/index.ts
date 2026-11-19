// The screen registry. Core path = the 15-minute talk; detours are reachable from the overview (O).
import { CantSee, Hook, Incidents, MeetAgent, StorageBasics, WhyMiss } from './ch1'
import { HarmTypes, Tickets, Twin } from './ch2'
import { FourAnswers, Idea, Pipeline } from './ch3'
import { ControlRoom, RealFiles } from './ch4'
import { Alternatives, DeepDives, Headline } from './ch5'
import { Really, Taught, Wrap } from './ch6'
import type { SlideDef } from './types'

export const CHAPTERS = [
  { n: 1, title: 'The problem' },
  { n: 2, title: 'The benchmark' },
  { n: 3, title: 'How it works' },
  { n: 4, title: 'Try it' },
  { n: 5, title: 'Results' },
  { n: 6, title: 'Reality check' },
  { n: 7, title: 'Wrap-up' },
]

export const SLIDES: SlideDef[] = [
  { id: '1.1', chapter: 1, title: 'One routine ticket', core: true, builds: 4, C: Hook },
  { id: '1.2', chapter: 1, title: 'It keeps happening', core: true, builds: 6, C: Incidents },
  { id: '1.3', chapter: 1, title: 'Storage in 60 seconds', core: true, builds: 7, C: StorageBasics },
  { id: '1.4', chapter: 1, title: 'Meet the AI admin', core: false, builds: 3, C: MeetAgent },
  { id: '1.5', chapter: 1, title: 'What the agent can’t see', core: true, builds: 2, C: CantSee },
  { id: '1.6', chapter: 1, title: 'Why today’s safety methods miss it', core: true, builds: 5, C: WhyMiss },
  { id: '2.1', chapter: 2, title: 'A pretend company to test on', core: true, builds: 3, C: Twin },
  { id: '2.2', chapter: 2, title: '150 tickets, 12 traps', core: true, builds: 1, C: Tickets },
  { id: '2.3', chapter: 2, title: 'How we judge harm', core: false, builds: 1, C: HarmTypes },
  { id: '3.1', chapter: 3, title: 'Try it on a copy first', core: true, builds: 3, C: Idea },
  { id: '3.2', chapter: 3, title: 'The pipeline, step by step', core: true, builds: 6, C: Pipeline },
  { id: '3.3', chapter: 3, title: 'Four possible answers', core: true, builds: 1, C: FourAnswers },
  { id: '4.1', chapter: 4, title: 'The control room', core: true, builds: 1, C: ControlRoom },
  { id: '4.2', chapter: 4, title: 'Real files', core: false, builds: 1, C: RealFiles },
  { id: '5.1', chapter: 5, title: 'Out of 100 tickets', core: true, builds: 3, C: Headline },
  { id: '5.2', chapter: 5, title: 'Against the alternatives', core: true, builds: 1, C: Alternatives },
  { id: '5.3', chapter: 5, title: 'Deep dives', core: false, builds: 1, C: DeepDives },
  { id: '6.1', chapter: 6, title: 'Did it really happen?', core: true, builds: 3, C: Really },
  { id: '6.2', chapter: 6, title: 'What reality taught us', core: false, builds: 1, C: Taught },
  { id: '7', chapter: 7, title: 'Three things to remember', core: true, builds: 4, C: Wrap },
]
