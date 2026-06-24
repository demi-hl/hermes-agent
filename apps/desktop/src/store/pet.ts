import { atom, computed } from 'nanostores'

import { $activeGatewayProfile, normalizeProfileKey } from '@/store/profile'
import { $busy } from '@/store/session'

/**
 * Petdex mascot state for the desktop floating pet.
 *
 * The spritesheet payload comes from the gateway `pet.info` RPC (shared with
 * the TUI). The animation *state* is derived here from the same activity
 * signals the chat already tracks, mirroring the priority order documented in
 * `agent/pet/state.py` so the Python and TS surfaces never drift.
 */

export type PetState = 'idle' | 'wave' | 'run' | 'failed' | 'review' | 'jump' | 'waiting'

export interface PetInfo {
  enabled: boolean
  slug?: string
  displayName?: string
  mime?: string
  spritesheetBase64?: string
  frameW?: number
  frameH?: number
  framesPerState?: number
  // Real (padding-trimmed) frame count per state row, from the engine. Lets the
  // canvas step only frames that exist instead of a fixed framesPerState, which
  // would animate into the transparent padding of ragged sheets (blank flash).
  framesByState?: Record<string, number>
  loopMs?: number
  scale?: number
  stateRows?: string[]
}

export interface PetActivity {
  busy?: boolean
  awaitingInput?: boolean
  toolRunning?: boolean
  reasoning?: boolean
  error?: boolean
  justCompleted?: boolean
  celebrate?: boolean
  // Coarse "some live gateway session is working" floor, polled from
  // session.active_list. Lets the pet react to ANY working session (e.g. a turn
  // driven from the TUI pane or a background session) rather than only the
  // focused desktop chat whose stream sets the fine-grained run/reason flags.
  anyWorking?: boolean
}

/**
 * Resolve the animation state from coarse activity signals.
 *
 * Priority (highest first) mirrors `agent.pet.state.derive_pet_state`:
 * error → celebrate → justCompleted → awaitingInput → toolRunning → reasoning →
 * busy → idle. `awaitingInput` (a clarify/approval blocking on the user) outranks
 * the in-flight signals because the turn is paused on you, not working.
 */
export function derivePetState(activity: PetActivity): PetState {
  if (activity.error) {
    return 'failed'
  }

  if (activity.celebrate) {
    return 'jump'
  }

  if (activity.justCompleted) {
    return 'wave'
  }

  if (activity.awaitingInput) {
    return 'waiting'
  }

  if (activity.toolRunning) {
    return 'run'
  }

  if (activity.reasoning) {
    return 'review'
  }

  if (activity.busy) {
    return 'run'
  }

  return 'idle'
}

export const $petInfo = atom<PetInfo>({ enabled: false })
export const $petActivity = atom<PetActivity>({})

/**
 * Profile the pet RPCs should resolve against. Pets are per-profile — the active
 * pet (`display.pet.*`) and the installed sprites live under each profile's
 * HERMES_HOME — so every pet RPC carries this. The gateway no-ops it for the
 * launch profile (own-profile backends already resolve it) and rebinds for any
 * other profile, which is what makes per-profile pets work in app-global remote
 * mode (one backend serving every profile).
 */
export function petProfile(): string {
  return normalizeProfileKey($activeGatewayProfile.get())
}

/**
 * Pet-local "you have a new message" flag, surfaced as the overlay's mail icon.
 * Deliberately not real unread tracking: it flips on when a turn finishes while
 * the app isn't focused, and off when the user opens the app via the mail icon
 * (or returns to the window). No persistence — it's a glance hint, not state.
 */
export const $petUnread = atom(false)
export const markPetUnread = () => $petUnread.set(true)
export const clearPetUnread = () => $petUnread.set(false)

/** Steady activity flags (toolRunning / reasoning) set + cleared by the stream. */
export const setPetActivity = (next: Partial<PetActivity>) =>
  $petActivity.set({ ...$petActivity.get(), ...next })

let flashTimer: ReturnType<typeof setTimeout> | undefined

/** Fire a transient reaction beat (error / celebrate / justCompleted) that
 *  decays back to the steady state after `ms`.
 *
 *  Each beat first clears its siblings so a stale one can't win the priority
 *  race: without this, a completion beat (`celebrate`) would merge on top of a
 *  lingering `error`, and `derivePetState` checks `error` first — so a clean
 *  finish would render the sad/failed pose. */
export const flashPetActivity = (next: Partial<PetActivity>, ms = 1600) => {
  setPetActivity({ celebrate: false, error: false, justCompleted: false, ...next })
  clearTimeout(flashTimer)
  flashTimer = setTimeout(
    () => setPetActivity({ celebrate: false, error: false, justCompleted: false }),
    ms
  )
}

/**
 * Cheap content signature for a `PetInfo`. The spritesheet base64 is large, so
 * we fingerprint it by length rather than value — a different sprite always has
 * a different byte count (and the slug changes too), which is enough to detect
 * a swap without diffing tens of KB on every poll.
 */
function petInfoSig(info: PetInfo): string {
  return [
    info.enabled ? '1' : '0',
    info.slug ?? '',
    info.scale ?? '',
    info.mime ?? '',
    info.frameW ?? '',
    info.frameH ?? '',
    info.framesPerState ?? '',
    info.loopMs ?? '',
    (info.stateRows ?? []).join(','),
    JSON.stringify(info.framesByState ?? {}),
    info.spritesheetBase64?.length ?? 0
  ].join('|')
}

let petInfoSigCache = petInfoSig($petInfo.get())

/**
 * Set the pet info, but skip the write when nothing meaningful changed. The
 * floating pet polls `pet.info` on an interval to pick up live config edits
 * (scale, slug); without this guard each steady-state poll would publish a new
 * object reference, breaking `PetSprite`'s memo and resetting the canvas (a
 * visible animation hitch) plus pushing a redundant frame to the pop-out
 * overlay. Dedupe by content signature so only real changes propagate.
 */
export const setPetInfo = (info: PetInfo) => {
  const sig = petInfoSig(info)
  if (sig === petInfoSigCache) {
    return
  }
  petInfoSigCache = sig
  $petInfo.set(info)
}

/**
 * The live pet state. Derives from the dedicated activity atom, falling back to
 * the always-present `$busy` chat signal so the pet reacts out of the box.
 *
 * `awaitingInput` (a clarify/approval blocking on the user) is an explicit flag
 * on `$petActivity` — set by the controller from `$attentionSessionIds` and
 * mirrored to the pop-out overlay through the same atom, so both surfaces agree
 * without the overlay needing the session list.
 */
export const $petState = computed(
  [$petActivity, $busy],
  (activity, busy): PetState => {
    // Busy floor: the focused chat's $busy, OR an explicit per-session busy, OR
    // the coarse "any gateway session is working" poll. The last makes the pet
    // animate for work driven outside the focused desktop chat (TUI pane,
    // background sessions) instead of sitting idle through it.
    const live = activity.busy ?? (busy || Boolean(activity.anyWorking))

    return derivePetState({
      busy: live,
      awaitingInput: activity.awaitingInput,
      // Steady flags only count mid-turn — ignore stale ones once at rest so an
      // interrupted turn can't pin the pet on `run`/`review`.
      toolRunning: live && activity.toolRunning,
      reasoning: live && activity.reasoning,
      error: activity.error,
      justCompleted: activity.justCompleted,
      celebrate: activity.celebrate
    })
  }
)
