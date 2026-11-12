import { useEffect } from 'react'
import type { AppNotification } from './types'
import type { NotificationsState } from './useNotifications'

export interface NotificationToasterProps {
  state: NotificationsState
  onNavigate?(link: string, notification: AppNotification): void
  /** How long a toast stays. Warnings and dangers stay until closed. Default 6 s. */
  durationMs?: number
  /** At most this many on screen; older ones wait in the bell. Default 3. */
  max?: number
}

/**
 * Pops up notifications that arrive while the user is on a screen — a reminder
 * coming due, an approval waiting. Mount once, near the app root.
 */
export function NotificationToaster({ state, onNavigate, durationMs = 6000, max = 3 }:
                                    NotificationToasterProps) {
  const shown = state.arrived.slice(0, max)
  return (
    <div className="nna-toasts" role="region" aria-label="New notifications" aria-live="polite">
      {shown.map((n) => (
        <Toast key={n.id} notification={n} durationMs={durationMs} onClose={state.clearArrived}
               onOpen={() => {
                 void state.markRead(n.id)
                 if (n.link) onNavigate?.(n.link, n)
               }} />
      ))}
    </div>
  )
}

function Toast({ notification: n, durationMs, onClose, onOpen }: {
  notification: AppNotification
  durationMs: number
  /** Stable (the hook's `clearArrived`), so the timer is not restarted on re-render. */
  onClose(id: string): void
  onOpen(): void
}) {
  const sticky = n.level === 'warning' || n.level === 'danger'
  const { id } = n
  useEffect(() => {
    if (sticky) return
    const timer = window.setTimeout(() => onClose(id), durationMs)
    return () => window.clearTimeout(timer)
  }, [sticky, durationMs, onClose, id])

  return (
    <div className={`nna-toast nna-toast-${n.level}`} role="status">
      <span className={`nna-dot nna-${n.level}`} aria-hidden />
      <button type="button" className="nna-toast-main" onClick={onOpen}>
        <span className="nna-item-title">{n.title}</span>
        {n.body && <span className="nna-item-body">{n.body}</span>}
      </button>
      <button type="button" className="nna-icon-button" aria-label="Close" onClick={() => onClose(id)}>
        ×
      </button>
    </div>
  )
}
