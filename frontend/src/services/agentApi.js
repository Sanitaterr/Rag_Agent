export async function fetchAgentSessions() {
  const response = await fetch(apiUrl('/agent/sessions'))
  if (!response.ok) {
    throw new Error((await response.text()) || `Request failed: ${response.status}`)
  }
  return response.json()
}

export async function fetchAgentSessionMessages(sessionId) {
  const response = await fetch(apiUrl(`/agent/sessions/${encodeURIComponent(sessionId)}/messages`))
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
  onDone,
  onError,
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
  })

  if (!response.ok || !response.body) {
    throw new Error((await response.text()) || `Request failed: ${response.status}`)
  }

  await readSseStream(response.body, {
    session: onSession,
    thought: onThought,
    tool: onTool,
    token: onToken,
    done: onDone,
    error: onError,
  })
}

function apiUrl(path) {
  const baseUrl = import.meta.env.VITE_API_BASE_URL || '/api'
  return `${baseUrl.replace(/\/$/, '')}${path}`
}

async function readSseStream(body, handlers) {
  const reader = body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

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
