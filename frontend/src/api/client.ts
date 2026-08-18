import { API_BASE_URL } from '../lib/env'
import { clearTokens, getAccessToken } from '../lib/tokenStorage'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE'
  body?: unknown
  auth?: boolean
}

/** Fired when a 401 is received so the app shell can bounce to /login. */
export const authEvents = new EventTarget()

export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, auth = true } = options
  const headers: Record<string, string> = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (auth) {
    const token = getAccessToken()
    if (token) headers['Authorization'] = `Bearer ${token}`
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })

  if (response.status === 401 && auth) {
    clearTokens()
    authEvents.dispatchEvent(new Event('unauthorized'))
  }

  if (response.status === 204) {
    return undefined as T
  }

  const text = await response.text()
  const data = text ? JSON.parse(text) : undefined

  if (!response.ok) {
    const message = data?.detail ? (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)) : response.statusText
    throw new ApiError(response.status, message)
  }

  return data as T
}
