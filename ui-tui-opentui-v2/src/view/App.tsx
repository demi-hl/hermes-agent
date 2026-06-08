/**
 * App — the Solid view shell (spec v4 §2 `view/App.tsx`). Header + a content zone
 * that is either the PAGER overlay (long slash output) or the normal
 * transcript + input zone; the input zone swaps the composer for a blocking-prompt
 * overlay when one is active. Fully themed via the ThemeProvider (§7.5).
 *
 *   header     flexShrink:0            (top chrome line)
 *   content    flexGrow:1, minHeight:0 — Pager OR (transcript + input zone)
 *   transcript flexGrow:1, minHeight:0 (the one <scrollbox>; §8 #2 gotchas)
 *   input zone flexShrink:0            (Composer, OR PromptOverlay when blocked)
 *
 * Overlays REPLACE rather than stack: a blocking prompt replaces the composer
 * (§8 #6 deadlock fix); the pager replaces transcript+composer. Replacing (not
 * hiding) means the composer remounts + refocuses when an overlay closes, and the
 * key that closed the overlay can't leak into it (the close is deferred a tick).
 */
import { Show } from 'solid-js'

import type { SessionStore } from '../logic/store.ts'
import { Composer } from './composer.tsx'
import { Header } from './header.tsx'
import { Pager } from './overlays/pager.tsx'
import { PromptOverlay } from './prompts/promptOverlay.tsx'
import { Transcript } from './transcript.tsx'

export interface AppProps {
  readonly store: SessionStore
  readonly onSubmit?: (text: string) => void
  readonly onRespond?: (method: string, params: Record<string, unknown>) => void
  readonly sessionId?: () => string | undefined
}

const NOOP = () => {}
const NOOP_RESPOND = () => {}
const NO_SESSION = () => undefined

export function App(props: AppProps) {
  const blocked = () => props.store.state.prompt !== undefined
  const pager = () => props.store.state.pager
  // Defer the close so the key that closed the overlay (Esc/q) can't land in the
  // freshly-remounted composer.
  const closePager = () => setTimeout(() => props.store.closePager(), 0)

  return (
    <box style={{ flexDirection: 'column', flexGrow: 1, padding: 1 }}>
      <Header store={props.store} />
      <Show
        when={pager()}
        fallback={
          <>
            <Transcript store={props.store} />
            <Show when={blocked()} fallback={<Composer onSubmit={props.onSubmit ?? NOOP} />}>
              <PromptOverlay
                store={props.store}
                onRespond={props.onRespond ?? NOOP_RESPOND}
                sessionId={props.sessionId ?? NO_SESSION}
              />
            </Show>
          </>
        }
      >
        {p => <Pager title={p().title} text={p().text} onClose={closePager} />}
      </Show>
    </box>
  )
}
