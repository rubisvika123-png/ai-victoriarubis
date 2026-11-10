#!/usr/bin/env node
// require-telegram-reply.js — предохранитель для Stop-хука.
//
// Назначение: не дать ходу завершиться, если в последнем витке пришло
// входящее сообщение из Telegram (dashi-channel), но агент так и не вызвал
// инструмент mcp__dashi-channel__reply. Вика читает ТОЛЬКО Telegram —
// текст, оставшийся в сессии, до неё не доходит.
//
// Как работает: Stop-хук получает на stdin JSON с transcript_path.
// Скрипт находит последнее входящее dashi-channel-сообщение и проверяет,
// был ли ПОСЛЕ него вызов reply. Если нет — возвращает decision:"block"
// с напоминанием. Как только reply вызван, следующий Stop проходит.
//
// Использование (в hooks.Stop): node <путь>/require-telegram-reply.js

const fs = require('fs');

function readStdin() {
  try {
    return fs.readFileSync(0, 'utf8');
  } catch (e) {
    return '';
  }
}

function main() {
  const raw = readStdin();
  let data;
  try {
    data = JSON.parse(raw);
  } catch (e) {
    process.exit(0); // нет валидного входа — не мешаем
  }

  // Второй заход подряд — значит мы уже разворачивали ход, а reply всё равно
  // не случился (например, Telegram недоступен). Дальше не держим: иначе агент
  // уходит в бесконечный круг и не отвечает вообще никому.
  if (data && data.stop_hook_active) process.exit(0);

  const tp = data && data.transcript_path;
  if (!tp || !fs.existsSync(tp)) process.exit(0);

  let lines;
  try {
    lines = fs.readFileSync(tp, 'utf8').split('\n').filter(Boolean);
  } catch (e) {
    process.exit(0);
  }

  const entries = [];
  for (const line of lines) {
    try {
      entries.push(JSON.parse(line));
    } catch (e) {
      /* пропускаем битые строки */
    }
  }

  // 1. Индекс последнего входящего dashi-channel сообщения.
  let lastInboundIdx = -1;
  for (let i = 0; i < entries.length; i++) {
    const obj = entries[i];
    if (!obj || obj.type !== 'user' || !obj.message) continue;
    const content = obj.message.content;
    let text = '';
    if (typeof content === 'string') {
      text = content;
    } else if (Array.isArray(content)) {
      for (const c of content) {
        if (c && c.type === 'text' && typeof c.text === 'string') text += c.text;
      }
    }
    if (text.includes('source="dashi-channel"')) lastInboundIdx = i;
  }

  if (lastInboundIdx === -1) process.exit(0); // не Telegram-виток — не мешаем

  // 2. Был ли ПОСЛЕ него вызов mcp__dashi-channel__reply.
  let replyAfter = false;
  for (let i = lastInboundIdx; i < entries.length; i++) {
    const obj = entries[i];
    if (!obj || obj.type !== 'assistant' || !obj.message) continue;
    const content = obj.message.content;
    if (!Array.isArray(content)) continue;
    for (const c of content) {
      if (c && c.type === 'tool_use' && c.name === 'mcp__dashi-channel__reply') {
        replyAfter = true;
      }
    }
  }

  if (!replyAfter) {
    process.stdout.write(
      JSON.stringify({
        decision: 'block',
        reason:
          'СТОП: пришло сообщение из Telegram (dashi-channel), а ответ через mcp__dashi-channel__reply так и не отправлен. Владелец видит ТОЛЬКО отправленное через reply — текст в терминале до него не доходит, и молчание выглядит как зависший агент. Вызови mcp__dashi-channel__reply с chat_id из входящего сообщения, потом завершай ход.',
      })
    );
  }
  process.exit(0);
}

main();
