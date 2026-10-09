import { API_BASE_URL } from '../lib/env'
import { clearTokens, getAccessToken } from '../lib/tokenStorage'

export class ApiError extends Error {
  status: number
  /** Machine-readable error code from the backend (e.g. NFZ / state-machine errors). */
  code: string | null
  /** Full JSON error body, e.g. { checks, report } for a blocked transition. */
  body: Record<string, unknown> | null
  constructor(status: number, message: string, code: string | null = null, body: Record<string, unknown> | null = null) {
    super(message)
    this.status = status
    this.code = code
    this.body = body
  }
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  body?: unknown
  auth?: boolean
  /** Return the raw response text instead of parsing JSON (file exports). */
  raw?: boolean
  /** Abort after this many ms so the UI never waits forever. */
  timeoutMs?: number
}

/** Fired when a 401 is received so the app shell can bounce to /login. */
export const authEvents = new EventTarget()

/** Turn FastAPI's `detail` (string | validation-error list | object) into a readable message. */
export function errorMessage(data: unknown, fallback: string): string {
  if (!data || typeof data !== 'object') return fallback
  const detail = (data as { detail?: unknown }).detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((e: { loc?: unknown[]; msg?: string }) => {
        const field = Array.isArray(e.loc) ? e.loc.filter((p) => p !== 'body' && p !== 'data').join('.') : ''
        return field ? `${field}: ${e.msg}` : e.msg
      })
      .join('; ')
  }
  const errors = (data as { errors?: { loc?: unknown[]; msg?: string }[] }).errors
  if (Array.isArray(errors)) return errorMessage({ detail: errors }, fallback)
  return detail ? JSON.stringify(detail) : fallback
}

export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, auth = true, raw = false, timeoutMs = 30_000 } = options
  const headers: Record<string, string> = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (auth) {
    const token = getAccessToken()
    if (token) headers['Authorization'] = `Bearer ${token}`
  }

  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeoutMs)
  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    })
  } catch (err) {
    const aborted = err instanceof DOMException && err.name === 'AbortError'
    throw new ApiError(0, aborted ? 'The request timed out' : 'Cannot reach the mission server (offline?)', aborted ? 'TIMEOUT' : 'NETWORK')
  } finally {
    window.clearTimeout(timer)
  }

  if (response.status === 401 && auth) {
    clearTokens()
    authEvents.dispatchEvent(new Event('unauthorized'))
  }

  if (response.status === 204) {
    return undefined as T
  }

  const text = await response.text()
  if (raw && response.ok) return text as T
  let data: unknown
  try {
    data = text ? JSON.parse(text) : undefined
  } catch {
    data = undefined
  }

  if (!response.ok) {
    const obj = data && typeof data === 'object' ? (data as Record<string, unknown>) : null
    const code = obj && typeof obj.code === 'string' ? obj.code : null
    throw new ApiError(response.status, errorMessage(data, response.statusText || 'Request failed'), code, obj)
  }

  return data as T
}
