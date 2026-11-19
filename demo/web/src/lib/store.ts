// Tiny cross-screen store: a ticket picked on one screen becomes the control room's ticket.
import { useSyncExternalStore } from 'react'

let picked: string | null = null
const subs = new Set<() => void>()

export function setPicked(id: string) {
  picked = id
  subs.forEach((f) => f())
}

export function usePicked(): string | null {
  return useSyncExternalStore((cb) => {
    subs.add(cb)
    return () => subs.delete(cb)
  }, () => picked)
}
