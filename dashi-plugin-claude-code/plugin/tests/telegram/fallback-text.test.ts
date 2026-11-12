import { describe, expect, test } from 'bun:test'
import { extractFallbackText, isServiceMessage } from '../../src/telegram/fallback-text'

describe('extractFallbackText', () => {
  test('collects list-like nested items in order', () => {
    const msg = {
      message_id: 1,
      from: { first_name: 'Vika' },
      chat: { id: 1, title: 'ignored' },
      checklist: { title: 'Excursions', tasks: [{ id: 1, text: 'Similans' }, { id: 2, text: 'Phi-Phi' }] },
    }
    expect(extractFallbackText(msg)).toBe('Excursions\nSimilans\nPhi-Phi')
  })

  test('renders a real rich_message list with labels', () => {
    const msg = {
      message_id: 1914,
      rich_message: {
        blocks: [
          { type: 'list', items: [
            { label: '1.', blocks: [{ type: 'paragraph', text: 'Similans' }], type: '1', value: 1 },
            { label: '2.', blocks: [{ type: 'paragraph', text: 'Phi-Phi' }], type: '1', value: 2 },
          ] },
          { type: 'paragraph', text: '' },
          { type: 'paragraph', text: 'Worth all of them?' },
        ],
      },
    }
    expect(extractFallbackText(msg)).toBe('1. Similans\n2. Phi-Phi\n\nWorth all of them?')
  })

  test('skips the quoted message and sender', () => {
    const msg = { reply_to_message: { text: 'old' }, from: { text: 'x' }, rich: { text: 'new' } }
    expect(extractFallbackText(msg)).toBe('new')
  })

  test('pin and join events are service messages, a list is not', () => {
    expect(isServiceMessage({ pinned_message: { text: 'x' } })).toBe(true)
    expect(isServiceMessage({ new_chat_members: [] })).toBe(true)
    expect(isServiceMessage({ checklist: { tasks: [] } })).toBe(false)
  })

  test('a shared contact card becomes readable text with name, phone and Telegram ID', () => {
    const msg = { message_id: 107, from: { id: 1 }, chat: { id: 1 },
      contact: { phone_number: '+79001112233', first_name: 'Артур', last_name: 'Райхман', user_id: 856320872 } }
    expect(extractFallbackText(msg)).toBe('Контакт: Артур Райхман, телефон +79001112233, Telegram ID 856320872')
  })

  test('a contact without Telegram account says so', () => {
    expect(extractFallbackText({ contact: { phone_number: '+7900', first_name: 'Ольга' } }))
      .toBe('Контакт: Ольга, телефон +7900, Telegram ID нет (человека нет в Телеграме или он скрыт)')
  })

  test('returns empty string when nothing readable', () => {
    expect(extractFallbackText({ message_id: 1, dice: { value: 3 } })).toBe('')
  })
})
