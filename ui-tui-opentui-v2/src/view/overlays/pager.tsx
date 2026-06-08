/**
 * Pager — a full-height scrollable text viewer (spec §2b `FloatBox` pager).
 * Porting it unlocks the long-output slash commands (/status /logs /history
 * /tools) at once. Replaces the transcript+composer while open (the App swaps it
 * in on `store.state.pager`).
 *
 * Scrolling is driven explicitly via `useKeyboard` → `scrollBy`/`scrollTo` (no
 * reliance on scrollbox auto-focus); Esc/q/Ctrl+C close. Carries the §8 #2
 * scrollbox gotchas (minHeight:0 wrapper+box, NO flexDirection on the box root).
 */
import { type ScrollBoxRenderable } from '@opentui/core'
import { useKeyboard } from '@opentui/solid'
import { For } from 'solid-js'

import { useTheme } from '../theme.tsx'

const PAGE = 10

export function Pager(props: { title: string; text: string; onClose: () => void }) {
  const theme = useTheme()
  let box: ScrollBoxRenderable | undefined
  const lines = () => props.text.split('\n')

  useKeyboard(key => {
    if (key.name === 'escape' || key.name === 'q' || (key.ctrl && key.name === 'c')) {
      props.onClose()
      return
    }
    if (!box) return
    if (key.name === 'up') box.scrollBy(-1)
    else if (key.name === 'down') box.scrollBy(1)
    else if (key.name === 'pageup') box.scrollBy(-PAGE)
    else if (key.name === 'pagedown') box.scrollBy(PAGE)
    else if (key.name === 'home') box.scrollTo(0)
    else if (key.name === 'end') box.scrollTo({ x: 0, y: box.scrollHeight })
  })

  return (
    <box style={{ borderColor: theme().color.accent, flexDirection: 'column', flexGrow: 1, minHeight: 0 }} border>
      <box style={{ flexShrink: 0, paddingLeft: 1 }}>
        <text fg={theme().color.accent}>
          <b>{props.title}</b>
        </text>
      </box>
      <box style={{ flexGrow: 1, minHeight: 0 }}>
        <scrollbox ref={el => (box = el)} style={{ flexGrow: 1, minHeight: 0 }}>
          <For each={lines()}>{line => <text fg={theme().color.text}>{line}</text>}</For>
        </scrollbox>
      </box>
      <box style={{ flexShrink: 0, paddingLeft: 1 }}>
        <text fg={theme().color.muted}>Esc/q close · ↑↓/PgUp/PgDn/Home/End scroll</text>
      </box>
    </box>
  )
}
