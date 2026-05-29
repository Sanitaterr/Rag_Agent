function apiUrl(path) {
  const baseUrl = import.meta.env.VITE_API_BASE_URL || '/api'
  return `${baseUrl.replace(/\/$/, '')}${path}`
}

export async function fetchAccessPointDevices({ status = '', keyword = '' } = {}) {
  const params = new URLSearchParams()
  if (status) params.set('status', status)
  if (keyword) params.set('keyword', keyword)

  const query = params.toString()
  const response = await fetch(apiUrl(`/access-points/devices${query ? `?${query}` : ''}`))
  if (!response.ok) {
    throw new Error((await response.text()) || `Request failed: ${response.status}`)
  }
  return response.json()
}

export async function fetchAccessPointDevice(deviceId) {
  const response = await fetch(apiUrl(`/access-points/devices/${encodeURIComponent(deviceId)}`))
  if (!response.ok) {
    throw new Error((await response.text()) || `Request failed: ${response.status}`)
  }
  return response.json()
}
