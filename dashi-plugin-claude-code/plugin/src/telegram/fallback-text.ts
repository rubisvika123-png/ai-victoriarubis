// Readable text from a message no specific handler took (Vika 2026-10-23:
// her list-formatted messages reached the bot but had no `text` field, so
// every message:* filter skipped them and the agent never saw them).
// Collects every string under a `text` / `title` / `caption` key, in order.

const TEXT_KEYS = new Set(['text', 'title', 'caption'])
const SKIP_KEYS = new Set(['from', 'chat', 'sender_chat', 'reply_to_message', 'forward_origin', 'entities', 'caption_entities'])

// Chat events (pin, join, title change…) are not something a person wrote.
const SERVICE_KEYS = [
  'pinned_message', 'new_chat_members', 'left_chat_member', 'new_chat_title', 'new_chat_photo',
  'delete_chat_photo', 'group_chat_created', 'supergroup_chat_created', 'migrate_to_chat_id',
  'migrate_from_chat_id', 'message_auto_delete_timer_changed', 'forum_topic_created',
  'forum_topic_edited', 'forum_topic_closed', 'forum_topic_reopened', 'video_chat_started',
  'video_chat_ended', 'video_chat_participants_invited', 'video_chat_scheduled', 'users_shared',
  'chat_shared', 'write_access_allowed', 'boost_added', 'chat_background_set',
]

export function isServiceMessage(message: Record<string, unknown>): boolean {
  return SERVICE_KEYS.some(k => k in message)
}

type RichBlock = { type?: string; text?: string; label?: string; blocks?: RichBlock[]; items?: RichBlock[] }

// rich_message (seen 2026-10-23): {blocks: [{type:'paragraph', text}, {type:'list', items:[{label:'1.', blocks:[…]}]}]}
function renderRich(blocks: RichBlock[], indent = ''): string[] {
  const lines: string[] = []
  for (const b of blocks) {
    if (typeof b.text === 'string') lines.push(indent + b.text)
    for (const item of b.items ?? []) {
      const inner = renderRich(item.blocks ?? [], indent + '   ')
      const first = (inner.shift() ?? '').trimStart()
      lines.push(`${indent}${item.label ?? '-'} ${first}`.trimEnd(), ...inner)
    }
    if (b.type !== 'list' && b.blocks) lines.push(...renderRich(b.blocks, indent))
  }
  return lines
}

type Contact = { first_name?: string; last_name?: string; phone_number?: string; user_id?: number; vcard?: string }

// A shared contact card has no text/title/caption at all (12.11.2026: the owner sent a
// person to the architect twice as a contact and the agent got «could not read»).
function renderContact(c: Contact): string {
  const name = [c.first_name, c.last_name].filter(Boolean).join(' ').trim()
  const parts = [`Контакт: ${name || 'без имени'}`]
  if (c.phone_number) parts.push(`телефон ${c.phone_number}`)
  if (typeof c.user_id === 'number') parts.push(`Telegram ID ${c.user_id}`)
  else parts.push('Telegram ID нет (человека нет в Телеграме или он скрыт)')
  return parts.join(', ')
}

export function extractFallbackText(message: unknown): string {
  const contact = (message as { contact?: Contact } | null)?.contact
  if (contact && typeof contact === 'object') {
    const caption = (message as { caption?: string }).caption
    return [renderContact(contact), typeof caption === 'string' ? caption.trim() : ''].filter(Boolean).join('\n')
  }
  const rich = (message as { rich_message?: { blocks?: RichBlock[] } } | null)?.rich_message
  if (rich && Array.isArray(rich.blocks)) {
    const text = renderRich(rich.blocks).join('\n').trim()
    if (text !== '') return text
  }
  const parts: string[] = []
  const walk = (node: unknown, depth: number): void => {
    if (depth > 16 || node === null || typeof node !== 'object') return
    if (Array.isArray(node)) {
      for (const item of node) walk(item, depth + 1)
      return
    }
    for (const [key, value] of Object.entries(node as Record<string, unknown>)) {
      if (SKIP_KEYS.has(key)) continue
      if (TEXT_KEYS.has(key) && typeof value === 'string' && value.trim() !== '') parts.push(value.trim())
      else walk(value, depth + 1)
    }
  }
  walk(message, 0)
  return parts.join('\n')
}
