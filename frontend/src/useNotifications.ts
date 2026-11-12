import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { NotificationsClient } from './client'
import type { AppNotification, NotificationLevel } from './types'

export interface UseNotificationsOptions {
  /** How often the unread count is asked for. Default 30 s; it is also asked on focus. */
  pollMs?: number
  /** Off while signed out, or on screens that should not poll. */
  enabled?: boolean
  /** How many of the newest notifications the drawer loads per page. */
  pageSize?: number
}

export interface NotificationsState {
  /** For the shell: `topBar={{ notifications: { count, urgent, onOpen } }}`. */
  count: number
  urgent: boolean
  byLevel: Partial<Record<NotificationLevel, number>>
  /** The drawer's list — empty until `load()`. */
  items: AppNotification[]
  hasMore: boolean
  loading: boolean
  error: Error | null
  /** Notifications that arrived while the app was open — what the toaster shows. */
  arrived: AppNotification[]
  load(): Promise<void>
  loadMore(): Promise<void>
  refresh(): Promise<void>
  markRead(id: string): Promise<void>
  markAllRead(): Promise<void>
  dismiss(id: string): Promise<void>
  clearArrived(id: string): void
}

/**
 * The bell's state, polled. Notifications already unread when the app opens are
 * counted but not toasted; ones that arrive afterwards go to `arrived` (newest first)
 * for `<NotificationToaster>`, and into the drawer's list if it is loaded.
 */
export function useNotifications(client: NotificationsClient,
                                 options: UseNotificationsOptions = {}): NotificationsState {
  const { pollMs = 30_000, enabled = true, pageSize = 20 } = options
  const [count, setCount] = useState(0)
  const [urgent, setUrgent] = useState(false)
  const [byLevel, setByLevel] = useState<Partial<Record<NotificationLevel, number>>>({})
  const [items, setItems] = useState<AppNotification[]>([])
  const [nextBefore, setNextBefore] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const [arrived, setArrived] = useState<AppNotification[]>([])
  const lastCount = useRef<number | null>(null)
  const seen = useRef(new Set<string>())
  const loaded = useRef(false)

  const refresh = useCallback(async () => {
    try {
      const summary = await client.unreadCount()
      setCount(summary.count)
      setUrgent(summary.urgent)
      setByLevel(summary.by_level)
      setError(null)
      const before = lastCount.current
      lastCount.current = summary.count
      if (before === null || summary.count <= before) return
      // Something new: fetch the newest unread and keep the ones not seen yet.
      const page = await client.list({ unreadOnly: true, limit: Math.min(summary.count - before, 5) })
      const fresh = page.items.filter((n) => !seen.current.has(n.id))
      fresh.forEach((n) => seen.current.add(n.id))
      if (fresh.length) {
        setArrived((old) => [...fresh, ...old].slice(0, 10))
        if (loaded.current) setItems((old) => [...fresh, ...old.filter((o) => !fresh.some((f) => f.id === o.id))])
      }
    } catch (e) {
      setError(e as Error)
    }
  }, [client])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const page = await client.list({ limit: pageSize })
      page.items.forEach((n) => seen.current.add(n.id))
      setItems(page.items)
      setNextBefore(page.next_before)
      loaded.current = true
      setError(null)
    } catch (e) {
      setError(e as Error)
    } finally {
      setLoading(false)
    }
  }, [client, pageSize])

  const loadMore = useCallback(async () => {
    if (!nextBefore) return
    setLoading(true)
    try {
      const page = await client.list({ limit: pageSize, before: nextBefore })
      page.items.forEach((n) => seen.current.add(n.id))
      setItems((old) => [...old, ...page.items])
      setNextBefore(page.next_before)
    } catch (e) {
      setError(e as Error)
    } finally {
      setLoading(false)
    }
  }, [client, nextBefore, pageSize])

  const markRead = useCallback(async (id: string) => {
    const now = new Date().toISOString()
    setItems((old) => old.map((n) => (n.id === id && !n.read_at ? { ...n, read_at: now } : n)))
    setArrived((old) => old.filter((n) => n.id !== id))
    await client.markRead(id)
    await refresh()
  }, [client, refresh])

  const markAllRead = useCallback(async () => {
    const now = new Date().toISOString()
    setItems((old) => old.map((n) => (n.read_at ? n : { ...n, read_at: now })))
    setArrived([])
    await client.markAllRead()
    await refresh()
  }, [client, refresh])

  const dismiss = useCallback(async (id: string) => {
    setItems((old) => old.filter((n) => n.id !== id))
    setArrived((old) => old.filter((n) => n.id !== id))
    await client.dismiss(id)
    await refresh()
  }, [client, refresh])

  const clearArrived = useCallback((id: string) => {
    setArrived((old) => old.filter((n) => n.id !== id))
  }, [])

  useEffect(() => {
    if (!enabled) return
    void refresh()
    const timer = window.setInterval(() => void refresh(), pollMs)
    const onVisible = () => {
      if (document.visibilityState === 'visible') void refresh()
    }
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('focus', onVisible)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('focus', onVisible)
    }
  }, [enabled, pollMs, refresh])

  return useMemo(() => ({
    count, urgent, byLevel, items, hasMore: Boolean(nextBefore), loading, error, arrived,
    load, loadMore, refresh, markRead, markAllRead, dismiss, clearArrived,
  }), [count, urgent, byLevel, items, nextBefore, loading, error, arrived, load, loadMore, refresh,
       markRead, markAllRead, dismiss, clearArrived])
}
