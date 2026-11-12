/**
 * In-app notifications for AIPMS apps.
 *
 *   import { createNotificationsClient, useNotifications, NotificationDrawer,
 *            NotificationToaster } from '@rodichub/notifications-and-alerts'
 *   import '@rodichub/notifications-and-alerts/styles.css'
 *
 *   const client = createNotificationsClient({ baseUrl: '/api/v1/docscomms/notifications',
 *                                              headers: () => authHeaders() })
 *   const bell = useNotifications(client)
 *   <ShellLayout topBar={{ notifications: { count: bell.count, urgent: bell.urgent,
 *                                           onOpen: () => setOpen(true) } }}>
 *   <NotificationDrawer state={bell} open={open} onClose={() => setOpen(false)} onNavigate={navigate} />
 *   <NotificationToaster state={bell} onNavigate={navigate} />
 */
export { createNotificationsClient, NotificationsError, timeAgo } from './client'
export type { ListOptions, NotificationsClient, NotificationsClientOptions } from './client'
export { useNotifications } from './useNotifications'
export type { NotificationsState, UseNotificationsOptions } from './useNotifications'
export { NotificationDrawer } from './NotificationDrawer'
export type { NotificationDrawerProps } from './NotificationDrawer'
export { NotificationToaster } from './NotificationToaster'
export type { NotificationToasterProps } from './NotificationToaster'
export type { AppNotification, NotificationLevel, NotificationPage, ReminderSummary, UnreadCount } from './types'
