import { useEffect } from 'react'
import { timeAgo } from './client'
import type { AppNotification } from './types'
import type { NotificationsState } from './useNotifications'

export interface NotificationDrawerProps {
  state: NotificationsState
  open: boolean
  onClose(): void
  /** Open a notification's link — e.g. react-router's `navigate`. */
  onNavigate?(link: string, notification: AppNotification): void
  title?: string
}

/**
 * The bell's panel, sliding in from the right. Opened from the shell TopBar's
 * `notifications.onOpen`; loads the list when it opens. Clicking a row marks it
 * read and follows its link; × hides it; Esc or the backdrop closes the panel.
 */
export function NotificationDrawer({ state, open, onClose, onNavigate, title = 'Notifications' }:
                                   NotificationDrawerProps) {
  const { load } = state
  useEffect(() => {
    if (open) void load()
  }, [open, load])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null
  const unread = state.items.some((n) => !n.read_at)

  return (
    <div className="nna-layer" role="presentation">
      <div className="nna-backdrop" onClick={onClose} />
      <aside className="nna-drawer" role="dialog" aria-modal="true" aria-label={title}>
        <header className="nna-drawer-head">
          <h2 className="nna-drawer-title">{title}</h2>
          {state.count > 0 && <span className="nna-count">{state.count} unread</span>}
          <div className="nna-spacer" />
          {unread && (
            <button type="button" className="nna-link" onClick={() => void state.markAllRead()}>
              Mark all read
            </button>
          )}
          <button type="button" className="nna-icon-button" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </header>

        <div className="nna-list">
          {state.error && !state.items.length && (
            <p className="nna-empty">Notifications could not be loaded — {state.error.message}</p>
          )}
          {!state.error && !state.loading && !state.items.length && (
            <p className="nna-empty">Nothing new. You will see reminders and alerts here.</p>
          )}
          {state.items.map((n) => (
            <article key={n.id} className={`nna-item${n.read_at ? '' : ' nna-unread'}`}>
              <button
                type="button"
                className="nna-item-main"
                onClick={() => {
                  if (!n.read_at) void state.markRead(n.id)
                  if (n.link) {
                    onNavigate?.(n.link, n)
                    onClose()
                  }
                }}
              >
                <span className={`nna-dot nna-${n.level}`} aria-hidden />
                <span className="nna-item-text">
                  <span className="nna-item-title">{n.title}</span>
                  {n.body && <span className="nna-item-body">{n.body}</span>}
                  <span className="nna-item-meta">
                    {timeAgo(n.created_at)}
                    {n.source ? ` · ${n.source}` : ''}
                  </span>
                </span>
              </button>
              <button type="button" className="nna-icon-button nna-dismiss" aria-label="Dismiss"
                      onClick={() => void state.dismiss(n.id)}>
                ×
              </button>
            </article>
          ))}
          {state.hasMore && (
            <button type="button" className="nna-more" disabled={state.loading}
                    onClick={() => void state.loadMore()}>
              {state.loading ? 'Loading…' : 'Show older'}
            </button>
          )}
        </div>
      </aside>
    </div>
  )
}
