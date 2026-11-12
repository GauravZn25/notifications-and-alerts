/** One in-app notification, as `GET …/notifications` returns it. */
export interface AppNotification {
  id: string
  title: string
  body: string
  level: NotificationLevel
  /** Where clicking it goes — an app path or a URL. */
  link: string | null
  category: string | null
  /** The module that raised it, e.g. "M14". */
  source: string | null
  entity_type: string | null
  entity_id: string | null
  data: Record<string, unknown>
  created_at: string
  read_at: string | null
}

export type NotificationLevel = 'info' | 'success' | 'warning' | 'danger'

export interface NotificationPage {
  items: AppNotification[]
  /** Pass as `before` for the next (older) page; null on the last page. */
  next_before: string | null
}

export interface UnreadCount {
  count: number
  /** Any unread warning or danger — the shell's bell turns red. */
  urgent: boolean
  by_level: Partial<Record<NotificationLevel, number>>
}

export interface ReminderSummary {
  id: string
  key: string | null
  title: string
  status: 'active' | 'paused' | 'completed' | 'cancelled'
  channels: Array<'email' | 'in_app'>
  /** Plain words, e.g. "daily at 09:00 (Asia/Kolkata)". */
  schedule: string
  next_run_at: string | null
  last_run_at: string | null
  runs_done: number
  last_error: string | null
}
