function apiUrl(path) {
  const baseUrl = import.meta.env.VITE_API_BASE_URL || '/api'
  return `${baseUrl.replace(/\/$/, '')}${path}`
}

export async function fetchKnowledgeFiles() {
  const response = await fetch(apiUrl('/knowledge/files'))
  if (!response.ok) {
    throw new Error((await response.text()) || `Request failed: ${response.status}`)
  }
  return response.json()
}

export async function fetchKnowledgePreview(fileId) {
  const response = await fetch(apiUrl(`/knowledge/files/${encodeURIComponent(fileId)}/preview`))
  if (!response.ok) {
    throw new Error((await response.text()) || `Request failed: ${response.status}`)
  }
  return response.json()
}

export async function uploadKnowledgeFile(file) {
  const formData = new FormData()
  formData.append('file', file)

  const response = await fetch(apiUrl('/knowledge/files'), {
    method: 'POST',
    body: formData,
  })
  if (!response.ok) {
    throw new Error((await response.text()) || `Request failed: ${response.status}`)
  }
  return response.json()
}

export async function deleteKnowledgeFile(fileId) {
  const response = await fetch(apiUrl(`/knowledge/files/${encodeURIComponent(fileId)}`), {
    method: 'DELETE',
  })
  if (!response.ok) {
    throw new Error((await response.text()) || `Request failed: ${response.status}`)
  }
  return response.json()
}

export async function startVectorizeFile(fileId) {
  const response = await fetch(apiUrl(`/knowledge/files/${encodeURIComponent(fileId)}/vectorize`), {
    method: 'POST',
  })
  if (!response.ok) {
    throw new Error((await response.text()) || `Request failed: ${response.status}`)
  }
  return response.json()
}

export async function fetchVectorizeJob(jobId) {
  const response = await fetch(apiUrl(`/knowledge/vectorize/jobs/${encodeURIComponent(jobId)}`))
  if (!response.ok) {
    throw new Error((await response.text()) || `Request failed: ${response.status}`)
  }
  return response.json()
}

export async function fetchGraphStats() {
  const response = await fetch(apiUrl('/knowledge/graph/stats'))
  if (!response.ok) {
    throw new Error((await response.text()) || `Request failed: ${response.status}`)
  }
  return response.json()
}

export async function fetchKnowledgeGraph(limit = 120, options = {}) {
  const params = new URLSearchParams({ limit: String(limit) })
  const response = await fetch(apiUrl(`/knowledge/graph/visualization?${params.toString()}`), {
    signal: options.signal,
  })
  if (!response.ok) {
    throw new Error((await response.text()) || `Request failed: ${response.status}`)
  }
  return response.json()
}

export async function initializeKnowledgeGraph() {
  const response = await fetch(apiUrl('/knowledge/graph/initialize'), {
    method: 'POST',
  })
  if (!response.ok) {
    throw new Error((await response.text()) || `Request failed: ${response.status}`)
  }
  return response.json()
}

export async function startGraphIndexFile(fileId) {
  const response = await fetch(apiUrl(`/knowledge/files/${encodeURIComponent(fileId)}/graph-index`), {
    method: 'POST',
  })
  if (!response.ok) {
    throw new Error((await response.text()) || `Request failed: ${response.status}`)
  }
  return response.json()
}
