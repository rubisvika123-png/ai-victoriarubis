// StatusManager — owns the transient "Печатает.../Думает.../🔧 tool" message
// the bot edits while Claude works on a reply. Mirrors gateway.py:2895-2930
// behaviour: lazy status message, periodic edit while task is in flight, delete
// (or finalize) before the real answer ships.
//
// Design notes:
//   * One active StatusHandle per chatId. Calling start() while another is
//     active silently cancels the previous one (no edit to "canceled" label —
//     the new status starts immediately and is what the user sees).
//   * Timers are injected via setTimer/clearTimer so tests can drive the
//     ticker with fake clocks. We never call global setInterval directly.
//   * Telegram edit failures are SWALLOWED (logged at warn). A flaky edit
//     must never propagate to a 500 in the tool layer — the real reply path
//     is the source of truth.
//   * TTL guard auto-cancels after `config.status.ttl_ms` so a stuck status
//     (e.g. Claude crashed without firing complete()) doesn't haunt the chat
//     forever.

import type { AppConfig } from '../config.js'
import type { Logger } from '../log.js'
import type { ChatAction, TelegramApi } from '../channel/tools.js'
import { escapeHtml } from '../format/html.js'
import type { ActivityStatusEvent } from '../hooks/claude-events.js'
import {
  buildActivityDetail,
  buildHumanizedActivityLine,
  maskSecrets,
  renderActivityBlock,
  type ActivityCall,
  type ActivitySnapshot,
} from './activity-renderer.js'

// Telegram's `sendChatAction` indicator expires after 5 s; re-pulse on a 4 s
// timer to keep the header animation continuous without spamming the API.
const CHAT_ACTION_PULSE_MS = 4000

// The ticker animates every interval_ms, but the message itself is edited at
// most this often. 13.10.2026: a 700 ms edit cadence during a multi-minute
// tool run got Stepan's bot a 15.5 h Telegram flood ban.
const TICK_MIN_EDIT_GAP_MS = 4000

// "429: Too Many Requests: retry after 55441" → 55441 (seconds), else null.
function retryAfterSeconds(err: unknown): number | null {
  const msg = err instanceof Error ? err.message : String(err)
  if (!/429|Too Many Requests/i.test(msg)) return null
  const m = /retry after (\d+)/i.exec(msg)
  return m ? Number(m[1]) : 1
}

// All active StatusStates map to a single `typing` action. Tool-specific
// actions (`upload_document`, etc.) are not used today because Telegram's
// header animation is the same for `typing` and we want to keep the contract
// simple. Extend here if a per-tool icon becomes worth the noise.
function chatActionFor(state: StatusState): ChatAction | null {
  switch (state.kind) {
    case 'typing':
    case 'thinking':
    case 'tool':
    case 'activity':
    case 'working':
      return 'typing'
    case 'stopped':
    case 'error':
      return null
  }
}

export type StatusState =
  | { kind: 'typing' }
  | { kind: 'thinking' }
  | { kind: 'tool'; toolName: string }
  | { kind: 'activity'; snapshot: ActivitySnapshot }
  | { kind: 'working' }
  | { kind: 'stopped'; reason?: string }
  | { kind: 'error'; reason?: string }

// Cap on the in-memory tool-call buffer per active chat. Gateway uses 10
// (gateway.py:1876-1879); render window is 5 (`activity-renderer.ts`
// `ACTIVITY_WINDOW`). Keeping more than we render lets the "+N earlier"
// summary stay accurate after collapses without an unbounded buffer.
const ACTIVITY_MAX_BUFFER = 10

// Throttle non-Agent tool edits to once per this many ms. Agent dispatches
// always edit immediately. Mirrors gateway.py:1738 `_last_tool_render` + 5 s
// throttle in `_TaskBoundaryTracker`.
const ACTIVITY_EDIT_THROTTLE_MS = 5000

export interface StatusHandle {
  readonly chatId: string
  readonly messageId: number
  readonly startedAt: number
}

// Telegram surface the manager actually touches. Pulled out of channel/tools
// TelegramApi so we can extend with deleteMessage without bloating that
// type's import graph elsewhere.
export interface TelegramApiForStatus {
  sendMessage: TelegramApi['sendMessage']
  editMessageText: TelegramApi['editMessageText']
  deleteMessage?: (chatId: string, messageId: number) => Promise<void>
  // Native Telegram `typing` indicator in the chat header. Optional so unit
  // tests can stub a minimal surface. Action expires after 5 s on Telegram's
  // side, so the manager re-pulses on a 4 s timer while a status is active.
  sendChatAction?: TelegramApi['sendChatAction']
}

export interface StatusManagerDeps {
  telegramApi: TelegramApiForStatus
  config: AppConfig
  log: Logger
  now?: () => number
  setTimer?: (cb: () => void, ms: number) => NodeJS.Timeout
  clearTimer?: (handle: NodeJS.Timeout) => void
}

interface InternalEntry {
  handle: StatusHandle
  state: StatusState
  // Cycle index used to advance the dot animation on each tick.
  tick: number
  lastText: string
  // When the message was last edited; the ticker skips edits closer than
  // TICK_MIN_EDIT_GAP_MS to it.
  lastEditAt: number
  intervalHandle: NodeJS.Timeout | null
  ttlHandle: NodeJS.Timeout | null
  // Separate cadence from the message edit ticker — see CHAT_ACTION_PULSE_MS.
  chatActionHandle: NodeJS.Timeout | null
  // Rolling activity state — kept on the entry even while state.kind!=
  // 'activity' so a Stop after a non-tool reasoning phase still has the
  // recorded history. Last-tool-render timestamp throttles non-Agent edits.
  activityCalls: ActivityCall[]
  activityPhase: 'reasoning' | 'tool'
  activityStartedAt: number
  lastToolRenderAt: number
  // Map tool_use_id → call buffer index, so PostToolUse Agent can update
  // the recorded line with a short done summary.
  toolUseIndex: Map<string, number>
}

// Reuse Telegram's "message is not modified" detection. We can't import a
// real grammY error class here (would couple status module to grammY); use
// a substring check that matches the wire payload Telegram returns.
function isMessageNotModifiedError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err)
  return /message is not modified/i.test(msg)
}

function renderState(state: StatusState, tick: number, nowMs: number): string {
  // Ellipsis animation for typing/thinking: 1→2→3 dots.
  const dotCount = (tick % 3) + 1
  const dots = '.'.repeat(dotCount)
  switch (state.kind) {
    case 'typing':
      return `<i>Печатает${dots}</i>`
    case 'thinking':
      return `<i>Думает${dots}</i>`
    case 'tool':
      return `<i>🔧 ${escapeHtml(state.toolName)}</i>`
    case 'activity':
      return renderActivityBlock(state.snapshot, nowMs)
    case 'working':
      return `<i>Ещё работаю${dots}</i>`
    case 'stopped': {
      const tail = state.reason ? `: ${escapeHtml(state.reason)}` : ''
      return `<i>Остановлено${tail}</i>`
    }
    case 'error': {
      const tail = state.reason ? `: ${escapeHtml(state.reason)}` : ''
      return `<i>Ошибка${tail}</i>`
    }
  }
}

export class StatusManager {
  private readonly telegramApi: TelegramApiForStatus
  private readonly config: AppConfig
  private readonly log: Logger
  private readonly now: () => number
  private readonly setTimer: (cb: () => void, ms: number) => NodeJS.Timeout
  private readonly clearTimer: (handle: NodeJS.Timeout) => void
  private readonly entries: Map<string, InternalEntry>
  // Epoch ms until which Telegram asked us to stop calling (429 retry_after).
  private floodUntil = 0

  constructor(deps: StatusManagerDeps) {
    this.telegramApi = deps.telegramApi
    this.config = deps.config
    this.log = deps.log
    this.now = deps.now ?? (() => Date.now())
    // Bind to global timers as a default so production code doesn't need to
    // pass anything. Tests inject deterministic fake timers.
    this.setTimer = deps.setTimer ?? ((cb, ms) => setTimeout(cb, ms))
    this.clearTimer = deps.clearTimer ?? ((h) => clearTimeout(h))
    this.entries = new Map()
  }

  isActive(chatId: string): boolean {
    return this.entries.has(chatId)
  }

  // List of active chat ids — used by shutdown to flush all status messages.
  activeChatIds(): string[] {
    return Array.from(this.entries.keys())
  }

  async start(
    chatId: string,
    replyToMessageId: number | undefined,
    initialState: StatusState = { kind: 'typing' },
  ): Promise<StatusHandle> {
    // Only one active status per chat at a time: silently drop the previous
    // one (delete its message if we can, else just stop its timers) so we
    // don't leak a stale "Печатает…" message on top of the new one. It's
    // about to be replaced 1:1 by the new status message below, so editing
    // it to a visible "Остановлено: superseded" label (the old behaviour)
    // only confused the owner — it reads as an error in a live chat.
    // Album-path callers in handlers.ts call start() once per album item;
    // without this terminate step the first item's pulse stays forever.
    const previous = this.entries.get(chatId)
    if (previous) {
      this.stopTimers(previous)
      this.entries.delete(chatId)
      if (this.telegramApi.deleteMessage) {
        try {
          await this.telegramApi.deleteMessage(chatId, previous.handle.messageId)
        } catch (err) {
          this.log.debug('superseded status delete failed (ignored)', {
            chat_id: chatId,
            message_id: previous.handle.messageId,
            error: err instanceof Error ? err.message : String(err),
          })
        }
      }
    }

    const text = renderState(initialState, 0, this.now())
    const sendOpts: { parse_mode: 'HTML'; reply_to_message_id?: number } = {
      parse_mode: 'HTML',
    }
    if (replyToMessageId !== undefined) sendOpts.reply_to_message_id = replyToMessageId

    let sent: { message_id: number }
    try {
      sent = await this.telegramApi.sendMessage(chatId, text, sendOpts)
    } catch (err) {
      // If we can't even send the initial status, log and rethrow — caller
      // can decide whether to proceed without status. (handlers.ts treats
      // status as best-effort and will catch this.)
      this.log.warn('status start failed', {
        chat_id: chatId,
        error: err instanceof Error ? err.message : String(err),
      })
      throw err
    }

    const handle: StatusHandle = {
      chatId,
      messageId: sent.message_id,
      startedAt: this.now(),
    }
    const entry: InternalEntry = {
      handle,
      state: initialState,
      tick: 0,
      lastText: text,
      lastEditAt: this.now(),
      intervalHandle: null,
      ttlHandle: null,
      chatActionHandle: null,
      activityCalls: [],
      activityPhase: 'reasoning',
      activityStartedAt: this.now(),
      // Sentinel — first non-Agent tool call always renders. Throttle only
      // applies AFTER a render has actually occurred.
      lastToolRenderAt: Number.NEGATIVE_INFINITY,
      toolUseIndex: new Map(),
    }
    this.entries.set(chatId, entry)

    // Fire-and-forget initial chat action so the Telegram header shows
    // `typing…` immediately, not on the first pulse 4 s in.
    void this.pulseChatAction(entry)
    entry.chatActionHandle = this.setTimer(
      () => this.chatActionTick(chatId, handle.messageId),
      CHAT_ACTION_PULSE_MS,
    )

    // Periodic tick — re-edit with advanced ellipsis (typing/thinking only)
    // or keep the same text for tool/stopped/error states. Same-text edits
    // collapse via the "message is not modified" swallow path below.
    const tick = (): void => {
      const live = this.entries.get(chatId)
      if (!live || live.handle.messageId !== handle.messageId) return
      live.tick += 1
      const next = renderState(live.state, live.tick, this.now())
      if (this.now() - live.lastEditAt >= TICK_MIN_EDIT_GAP_MS) {
        void this.editSafely(live, next)
      }
      // Re-arm next tick.
      live.intervalHandle = this.setTimer(tick, this.config.status.interval_ms)
    }
    entry.intervalHandle = this.setTimer(tick, this.config.status.interval_ms)

    // After ttl_ms a long task is NOT a stopped one (Вика 07.10.2026: «Остановлено»
    // after 5 minutes read as a frozen agent while it was still working). Switch
    // the label to «Ещё работаю…» and keep it until the reply removes it; only a
    // hard cap of 12 x ttl_ms finally gives up, so a truly stuck status can't
    // pulse forever.
    entry.ttlHandle = this.setTimer(() => {
      const live = this.entries.get(chatId)
      if (!live || live.handle.messageId !== handle.messageId) return
      live.state = { kind: 'working' }
      live.tick = 0
      void this.editSafely(live, renderState(live.state, 0, this.now()))
      live.ttlHandle = this.setTimer(() => {
        const still = this.entries.get(chatId)
        if (!still || still.handle.messageId !== handle.messageId) return
        void this.cancel(chatId, 'ttl')
      }, this.config.status.ttl_ms * 11)
    }, this.config.status.ttl_ms)

    return handle
  }

  async update(handle: StatusHandle, state: StatusState): Promise<void> {
    const entry = this.entries.get(handle.chatId)
    if (!entry || entry.handle.messageId !== handle.messageId) {
      // Stale handle — caller is editing something already completed.
      // Drop silently; the original status is gone.
      return
    }
    entry.state = state
    // Reset tick on state change so the ellipsis animation restarts at 1.
    entry.tick = 0
    const text = renderState(state, 0, this.now())
    await this.editSafely(entry, text)
  }

  // Convenience for the MCP `status` tool: the agent only has the chat_id,
  // not a StatusHandle (which is internal to the gateAndNotify caller). This
  // lets the tool re-target whichever status is currently active for the chat.
  async updateByChatId(chatId: string, state: StatusState): Promise<void> {
    const entry = this.entries.get(chatId)
    if (!entry) return
    await this.update(entry.handle, state)
  }

  /**
   * Apply a Claude Code hook event to the chat's status. Opens a new status
   * with no `reply_to_message_id` if none is active (SessionStart /
   * UserPromptSubmit / PreToolUse all act as openers — gateway parity).
   * Stop completes via `complete()` so existing delete_on_complete behaviour
   * holds.
   *
   * Non-Agent PreToolUse calls are recorded immediately but the on-Telegram
   * edit is throttled to once per 5 s; the next reasoning/Agent/Stop event
   * flushes the buffer (mirrors gateway.py:1738 `_last_tool_render`).
   */
  async recordActivityByChatId(chatId: string, event: ActivityStatusEvent): Promise<void> {
    // Stop closes the session. Use existing complete() so delete_on_complete
    // semantics + timer cleanup stay in one place.
    if (event.kind === 'session_stop') {
      await this.complete(chatId)
      return
    }

    // Lazy-open: hook events can arrive before any inbound Telegram message.
    // In that case we open a status with no reply target so the rendering
    // surface exists. Initial state is `activity` so the first edit shows
    // the working block, not "Печатает…".
    let entry = this.entries.get(chatId)
    if (!entry) {
      const initial: StatusState = {
        kind: 'activity',
        snapshot: this.buildSnapshot([], 'reasoning', this.now()),
      }
      try {
        await this.start(chatId, undefined, initial)
      } catch {
        // Telegram send failed — already logged inside start(). Visibility
        // failure must not back-pressure Claude hooks; bail silently.
        return
      }
      entry = this.entries.get(chatId)
      if (!entry) return
    }

    const now = this.now()
    let shouldRender = false
    let isAgentEvent = false

    switch (event.kind) {
      case 'session_start':
      case 'reasoning': {
        entry.activityPhase = 'reasoning'
        shouldRender = true
        break
      }
      case 'tool_start': {
        // Record every call up to the buffer cap so PostToolUse can pair.
        const detail = buildActivityDetail(event.toolName, event.toolInput)
        const humanized = buildHumanizedActivityLine(event.toolName, event.toolInput)
        const call: ActivityCall = { toolName: event.toolName, detail, humanized }
        entry.activityCalls.push(call)
        if (entry.activityCalls.length > ACTIVITY_MAX_BUFFER) {
          entry.activityCalls.shift()
          // After shifting, indexes in toolUseIndex are stale; rebuild
          // lazily — only PostToolUse readers touch them.
          const rebuilt = new Map<string, number>()
          for (const [k, v] of entry.toolUseIndex.entries()) {
            if (v > 0) rebuilt.set(k, v - 1)
          }
          entry.toolUseIndex = rebuilt
        }
        entry.toolUseIndex.set(event.toolUseId, entry.activityCalls.length - 1)
        entry.activityPhase = 'tool'

        isAgentEvent = event.toolName === 'Agent'
        if (isAgentEvent) {
          shouldRender = true
        } else if (now - entry.lastToolRenderAt >= ACTIVITY_EDIT_THROTTLE_MS) {
          shouldRender = true
        } else {
          // Throttled — buffer remains; next render flush will surface it.
          shouldRender = false
        }
        break
      }
      case 'tool_end': {
        entry.activityPhase = 'reasoning'
        // For Agent PostToolUse, attach a short done summary to the matching
        // line — capped at 30 chars + masked AT STORE TIME (review §7).
        // Pre-fix the raw tool_result lived in the buffer until render; an
        // in-memory leak (debug dump, future log sink, serializer) would
        // surface raw tokens. Masking here means the buffer never holds an
        // unmasked secret-shaped string.
        if (event.toolName === 'Agent' && event.toolResult !== undefined) {
          const idx = entry.toolUseIndex.get(event.toolUseId)
          if (idx !== undefined && idx >= 0 && idx < entry.activityCalls.length) {
            const rawSummary =
              typeof event.toolResult === 'string'
                ? event.toolResult.slice(0, 30)
                : ''
            const summary = rawSummary ? maskSecrets(rawSummary) : ''
            if (summary) {
              const prev = entry.activityCalls[idx]
              if (prev) {
                entry.activityCalls[idx] = {
                  toolName: prev.toolName,
                  detail: `${prev.detail} — ${summary}`,
                  humanized:
                    prev.humanized !== null
                      ? `${prev.humanized} — ${escapeHtml(summary)}`
                      : null,
                }
              }
            }
          }
        }
        shouldRender = true
        break
      }
    }

    if (!shouldRender) return

    if (event.kind === 'tool_start' && !isAgentEvent) {
      entry.lastToolRenderAt = now
    } else if (event.kind === 'tool_start' && isAgentEvent) {
      entry.lastToolRenderAt = now
    }

    const snapshot = this.buildSnapshot(
      entry.activityCalls,
      entry.activityPhase,
      entry.activityStartedAt,
    )
    entry.state = { kind: 'activity', snapshot }
    entry.tick = 0
    const text = renderState(entry.state, 0, now)
    await this.editSafely(entry, text)
  }

  private buildSnapshot(
    calls: ReadonlyArray<ActivityCall>,
    phase: 'reasoning' | 'tool',
    startedAtMs: number,
  ): ActivitySnapshot {
    return {
      startedAtMs,
      // Defensive copy so consumers don't see future mutations.
      calls: calls.slice(),
      phase,
    }
  }

  async complete(chatId: string): Promise<void> {
    const entry = this.entries.get(chatId)
    if (!entry) return
    this.stopTimers(entry)
    this.entries.delete(chatId)
    if (this.config.status.delete_on_complete && this.telegramApi.deleteMessage) {
      try {
        await this.telegramApi.deleteMessage(chatId, entry.handle.messageId)
      } catch (err) {
        // Stale message, deleted by user, or permission issue — never fatal.
        this.log.debug('status delete failed (ignored)', {
          chat_id: chatId,
          message_id: entry.handle.messageId,
          error: err instanceof Error ? err.message : String(err),
        })
      }
    }
  }

  async cancel(chatId: string, reason: string): Promise<void> {
    const entry = this.entries.get(chatId)
    if (!entry) return
    this.stopTimers(entry)
    this.entries.delete(chatId)
    const text = renderState({ kind: 'stopped', reason }, 0, this.now())
    try {
      await this.telegramApi.editMessageText(
        chatId,
        entry.handle.messageId,
        text,
        { parse_mode: 'HTML' },
      )
    } catch (err) {
      if (!isMessageNotModifiedError(err)) {
        this.log.warn('status cancel edit failed', {
          chat_id: chatId,
          message_id: entry.handle.messageId,
          error: err instanceof Error ? err.message : String(err),
        })
      }
    }
  }

  // ───── internals ─────

  private async editSafely(entry: InternalEntry, text: string): Promise<void> {
    // Inside a flood ban every extra call is wasted (and keeps the bot on
    // Telegram's radar) — stay silent until retry_after has passed.
    if (this.now() < this.floodUntil) return
    if (text === entry.lastText) {
      // Skip the network roundtrip; Telegram would respond with "message is
      // not modified" anyway and we'd swallow it.
      return
    }
    try {
      await this.telegramApi.editMessageText(
        entry.handle.chatId,
        entry.handle.messageId,
        text,
        { parse_mode: 'HTML' },
      )
      entry.lastText = text
      entry.lastEditAt = this.now()
    } catch (err) {
      if (this.noteFlood(err)) return
      if (isMessageNotModifiedError(err)) {
        // Treat as success — sync our local cache so we don't retry next tick.
        entry.lastText = text
        return
      }
      this.log.warn('status edit failed (ignored)', {
        chat_id: entry.handle.chatId,
        message_id: entry.handle.messageId,
        error: err instanceof Error ? err.message : String(err),
      })
    }
  }

  private stopTimers(entry: InternalEntry): void {
    if (entry.intervalHandle !== null) {
      this.clearTimer(entry.intervalHandle)
      entry.intervalHandle = null
    }
    if (entry.ttlHandle !== null) {
      this.clearTimer(entry.ttlHandle)
      entry.ttlHandle = null
    }
    if (entry.chatActionHandle !== null) {
      this.clearTimer(entry.chatActionHandle)
      entry.chatActionHandle = null
    }
  }

  // Send a single chat action for the entry's current state. Swallows
  // errors — the header indicator is best-effort and a flaky call must
  // not derail the message edit path or surface to the agent.
  private async pulseChatAction(entry: InternalEntry): Promise<void> {
    if (!this.telegramApi.sendChatAction) return
    const action = chatActionFor(entry.state)
    if (action === null) return
    if (this.now() < this.floodUntil) return
    try {
      await this.telegramApi.sendChatAction(entry.handle.chatId, action)
    } catch (err) {
      if (this.noteFlood(err)) return
      this.log.debug('sendChatAction failed (ignored)', {
        chat_id: entry.handle.chatId,
        action,
        error: err instanceof Error ? err.message : String(err),
      })
    }
  }

  // On a 429 remember until when Telegram wants silence. Returns true if err
  // was a flood error (already logged here).
  private noteFlood(err: unknown): boolean {
    const seconds = retryAfterSeconds(err)
    if (seconds === null) return false
    const until = this.now() + (seconds + 1) * 1000
    if (until > this.floodUntil) {
      this.floodUntil = until
      this.log.warn('status paused: telegram flood limit', { retry_after_s: seconds })
    }
    return true
  }

  private chatActionTick(chatId: string, messageId: number): void {
    const live = this.entries.get(chatId)
    if (!live || live.handle.messageId !== messageId) return
    void this.pulseChatAction(live)
    live.chatActionHandle = this.setTimer(
      () => this.chatActionTick(chatId, messageId),
      CHAT_ACTION_PULSE_MS,
    )
  }
}
