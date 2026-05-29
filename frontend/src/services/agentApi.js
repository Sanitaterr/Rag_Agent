export async function fetchAgentSessions(options = {}) {
  const response = await fetch(apiUrl('/agent/sessions'), {
    signal: options.signal,
  })
  if (!response.ok) {
    throw new Error((await response.text()) || `Request failed: ${response.status}`)
  }
  return response.json()
}

export async function fetchAgentSessionMessages(sessionId, options = {}) {
  const response = await fetch(apiUrl(`/agent/sessions/${encodeURIComponent(sessionId)}/messages`), {
    signal: options.signal,
  })
  if (!response.ok) {
    throw new Error((await response.text()) || `Request failed: ${response.status}`)
  }
  return response.json()
}

export async function deleteAgentSession(sessionId, options = {}) {
  const response = await fetch(apiUrl(`/agent/sessions/${encodeURIComponent(sessionId)}`), {
    method: 'DELETE',
    signal: options.signal,
  })
  if (!response.ok) {
    throw new Error((await response.text()) || `Request failed: ${response.status}`)
  }
  return response.json()
}

export async function streamAgentMessage({
  message,
  sessionId,
  onSession,
  onThought,
  onTool,
  onToken,
  onAnswer,
  onDone,
  onError,
  signal,
}) {
  const response = await fetch(apiUrl('/agent/chat/stream'), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    },
    body: JSON.stringify({
      message,
      session_id: sessionId || null,
    }),
    signal,
  })

  if (!response.ok || !response.body) {
    throw new Error((await response.text()) || `Request failed: ${response.status}`)
  }

  await readSseStream(response.body, {
    session: onSession,
    thought: onThought,
    tool: onTool,
    token: onToken,
    answer: onAnswer,
    done: onDone,
    error: onError,
  }, signal)
}

function apiUrl(path) {
  const baseUrl = import.meta.env.VITE_API_BASE_URL || '/api'
  return `${baseUrl.replace(/\/$/, '')}${path}`
}

async function readSseStream(body, handlers, signal) {
  const reader = body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  const abortReader = () => reader.cancel().catch(() => {})

  if (signal?.aborted) {
    abortReader()
    return
  }
  signal?.addEventListener('abort', abortReader, { once: true })

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const frames = buffer.split('\n\n')
      buffer = frames.pop() || ''

      for (const frame of frames) {
        dispatchSseFrame(frame, handlers)
      }
    }

    if (buffer.trim()) {
      dispatchSseFrame(buffer, handlers)
    }
  } finally {
    signal?.removeEventListener('abort', abortReader)
    reader.releaseLock()
  }
}

function dispatchSseFrame(frame, handlers) {
  const lines = frame.split('\n')
  const eventName = lines.find((line) => line.startsWith('event:'))?.slice(6).trim()
  const dataText = lines
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).trimStart())
    .join('\n')

  if (!eventName || !dataText) return

  const payload = JSON.parse(dataText)
  handlers[eventName]?.(payload.value)
}
