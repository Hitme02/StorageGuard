// The minimal deck is the default. The earlier 20-screen control room is kept at ?classic.
import { StrictMode, type ComponentType } from 'react'
import { createRoot } from 'react-dom/client'

const classic = new URLSearchParams(window.location.search).has('classic')

async function load(): Promise<ComponentType> {
  if (classic) {
    await Promise.all([
      import('@fontsource/barlow/400.css'), import('@fontsource/barlow/500.css'), import('@fontsource/barlow/600.css'),
      import('@fontsource/barlow-condensed/500.css'), import('@fontsource/barlow-condensed/600.css'),
      import('@fontsource/jetbrains-mono/400.css'), import('./styles.css'),
    ])
    return (await import('./app/App')).default
  }
  await Promise.all([
    import('@fontsource/ibm-plex-sans/latin-300.css'), import('@fontsource/ibm-plex-sans/latin-400.css'),
    import('@fontsource/ibm-plex-sans/latin-500.css'), import('@fontsource/ibm-plex-mono/latin-400.css'),
    import('./deck/deck.css'),
  ])
  return (await import('./deck/Deck')).default
}

load().then((App) => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
})
