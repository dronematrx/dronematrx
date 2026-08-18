import { create } from 'zustand'
import * as authApi from '../api/auth'
import { authEvents } from '../api/client'
import type { User } from '../api/types'
import { clearTokens, getAccessToken, setTokens } from '../lib/tokenStorage'

interface AuthState {
  user: User | null
  status: 'idle' | 'loading' | 'authenticated' | 'unauthenticated'
  error: string | null
  login: (email: string, password: string) => Promise<void>
  logout: () => void
  bootstrap: () => Promise<void>
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  status: 'idle',
  error: null,

  login: async (email, password) => {
    set({ status: 'loading', error: null })
    try {
      const res = await authApi.login(email, password)
      setTokens(res.access_token, res.refresh_token)
      set({ user: res.user, status: 'authenticated', error: null })
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Login failed'
      set({ status: 'unauthenticated', error: message })
      throw err
    }
  },

  logout: () => {
    clearTokens()
    set({ user: null, status: 'unauthenticated' })
  },

  bootstrap: async () => {
    if (!getAccessToken()) {
      set({ status: 'unauthenticated' })
      return
    }
    set({ status: 'loading' })
    try {
      const user = await authApi.me()
      set({ user, status: 'authenticated' })
    } catch {
      clearTokens()
      set({ user: null, status: 'unauthenticated' })
    }
  },
}))

authEvents.addEventListener('unauthorized', () => {
  useAuthStore.setState({ user: null, status: 'unauthenticated' })
})
