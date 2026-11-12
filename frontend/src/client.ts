import type { AppNotification, NotificationPage, ReminderSummary, UnreadCount } from './types'

export interface NotificationsClientOptions {
  /** The router's base, e.g. "/api/v1/docscomms/notifications". */
  baseUrl: string
  /** The app's auth headers (bearer token, tenant…), fresh for every call. */
  headers?: () => HeadersInit | Promise<HeadersInit>
  /** Defaults to the global fetch. */
  fetch?: typeof fetch
}

export interface ListOptions {
  unreadOnly?: boolean
  category?: string
  before?: string | null
  limit?: number
}

export interface NotificationsClient {
  list(options?: ListOptions): Promise<NotificationPage>
  unreadCount(): Promise<UnreadCount>
  markRead(id: string): Promise<void>
  markAllRead(): Promise<void>
  dismiss(id: string): Promise<void>
  reminders(status?: ReminderSummary['status']): Promise<ReminderSummary[]>
  cancelReminder(id: string): Promise<void>
}

export class NotificationsError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'NotificationsError'
    this.status = status
  }
}

/** A thin client over the `create_router` API. Every call carries `headers()`. */
export function createNotificationsClient(options: NotificationsClientOptions): NotificationsClient {
  const base = options.baseUrl.replace(/\/+$/, '')
  const doFetch = options.fetch ?? ((...args: Parameters<typeof fetch>) => fetch(...args))

  async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers((await options.headers?.()) ?? {})
    headers.set('Accept', 'application/json')
    const response = await doFetch(`${base}${path}`, { ...init, headers })
    if (!response.ok) {
      let detail = response.statusText
      try {
        const body = await response.json()
        detail = typeof body?.detail === 'string' ? body.detail : JSON.stringify(body?.detail ?? body)
      } catch {
        /* not JSON */
      }
      throw new NotificationsError(response.status, detail || `HTTP ${response.status}`)
    }
    if (response.status === 204) return undefined as T
    return (await response.json()) as T
  }

  return {
    list({ unreadOnly, category, before, limit } = {}) {
      const query = new URLSearchParams()
      if (unreadOnly) query.set('unread_only', 'true')
      if (category) query.set('category', category)
      if (before) query.set('before', before)
      if (limit) query.set('limit', String(limit))
      const qs = query.toString()
      return call<NotificationPage>(qs ? `?${qs}` : '')
    },
    unreadCount: () => call<UnreadCount>('/unread-count'),
    markRead: async (id) => void (await call(`/${encodeURIComponent(id)}/read`, { method: 'POST' })),
    markAllRead: async () => void (await call('/read-all', { method: 'POST' })),
    dismiss: async (id) => void (await call(`/${encodeURIComponent(id)}/dismiss`, { method: 'POST' })),
    reminders: (status) => call<ReminderSummary[]>(status ? `/reminders?status=${status}` : '/reminders'),
    cancelReminder: async (id) =>
      void (await call(`/reminders/${encodeURIComponent(id)}/cancel`, { method: 'POST' })),
  }
}

/** "just now", "5 min ago", "3 h ago", "2 d ago", else the date. */
export function timeAgo(iso: string, now: number = Date.now()): string {
  // The API may send UTC without an offset (SQLite); read it as UTC.
  const stamp = /[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`
  const seconds = Math.max(0, Math.round((now - new Date(stamp).getTime()) / 1000))
  if (seconds < 45) return 'just now'
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`
  if (seconds < 7 * 86400) return `${Math.round(seconds / 86400)} d ago`
  return new Date(stamp).toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' })
}

export type { AppNotification }
