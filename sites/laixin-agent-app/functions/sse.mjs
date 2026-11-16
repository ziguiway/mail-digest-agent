// Copy this framing helper into each package that parses SSE (Function/browser).
export async function* readSSE(body, { maxFrameChars = 262144, signal } = {}) {
  const reader = body.getReader();
  const abort = () => { void reader.cancel().catch(() => {}); };
  signal?.addEventListener('abort', abort, { once: true });
  const decoder = new TextDecoder();
  let buffer = '', data = [], event = '', id = '', size = 0;
  try {
    while (true) {
      if (signal?.aborted) throw new Error('sse_aborted');
      const chunk = await reader.read();
      if (signal?.aborted) throw new Error('sse_aborted');
      buffer += chunk.done ? decoder.decode() : decoder.decode(chunk.value, { stream: true });
      while (true) {
        if (signal?.aborted) throw new Error('sse_aborted');
        const match = /[\r\n]/.exec(buffer);
        if (!match || (!chunk.done && buffer[match.index] === '\r' && match.index === buffer.length - 1)) break;
        const line = buffer.slice(0, match.index);
        const width = buffer[match.index] === '\r' && buffer[match.index + 1] === '\n' ? 2 : 1;
        buffer = buffer.slice(match.index + width);
        size += line.length;
        if (size > maxFrameChars) throw new Error('sse_frame_too_large');
        if (!line) {
          if (data.length) yield { event: event || 'message', id, data: data.join('\n') };
          data = []; event = ''; id = ''; size = 0;
        } else if (!line.startsWith(':')) {
          const colon = line.indexOf(':');
          const field = colon < 0 ? line : line.slice(0, colon);
          let value = colon < 0 ? '' : line.slice(colon + 1);
          if (value.startsWith(' ')) value = value.slice(1);
          if (field === 'data') data.push(value);
          else if (field === 'event') event = value;
          else if (field === 'id' && !value.includes('\0')) id = value;
        }
      }
      if (buffer.length + size > maxFrameChars) throw new Error('sse_frame_too_large');
      if (chunk.done) break;
    }
    // An unfinished frame at EOF is not a completed event.
  } finally {
    signal?.removeEventListener('abort', abort);
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export function encodeSSE(event, data) {
  return new TextEncoder().encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
}

// For the application's projected SSE, not raw QCA events. Returning true means
// the callback has handled an explicit terminal state (success OR failure).
export async function consumeSSE(response, onFrame, options = {}) {
  if (!response.ok || !response.body
    || response.headers.get('content-type')?.split(';')[0].trim().toLowerCase() !== 'text/event-stream') {
    await response.body?.cancel().catch(() => {});
    throw new Error('sse_response_invalid');
  }
  for await (const frame of readSSE(response.body, options)) {
    if (await onFrame(frame) === true) return;
  }
  throw new Error('sse_incomplete');
}
