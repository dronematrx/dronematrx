import { apiFetch } from './client'
import type { TokenResponse, User } from './types'

export function login(email: string, password: string): Promise<TokenResponse> {
  return apiFetch<TokenResponse>('/auth/login', { method: 'POST', body: { email, password }, auth: false })
}

export function me(): Promise<User> {
  return apiFetch<User>('/auth/me')
}
