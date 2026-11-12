import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createNotificationsClient, NotificationsError, timeAgo } from '../src/client.ts'

function fakeFetch(answers: Array<{ status: number; body?: unknown }>) {
  const calls: Array<{ url: string; method: string; headers: Headers }> = []
  const impl = async (url: string | URL | Request, init?: RequestInit) => {
    calls.push({ url: String(url), method: init?.method ?? 'GET', headers: new Headers(init?.headers) })
    const answer = answers.shift() ?? { status: 200, body: {} }
    return new Response(answer.status === 204 ? null : JSON.stringify(answer.body ?? {}),
                        { status: answer.status, headers: { 'Content-Type': 'application/json' } })
  }
  return { calls, impl: impl as typeof fetch }
}

test('calls the router with the app headers', async () => {
  const { calls, impl } = fakeFetch([
    { status: 200, body: { items: [], next_before: null } },
    { status: 200, body: { count: 2, urgent: true, by_level: { danger: 2 } } },
    { status: 200, body: { marked: 1 } },
    { status: 204 },
  ])
  const client = createNotificationsClient({
    baseUrl: '/api/v1/docscomms/notifications/', fetch: impl,
    headers: async () => ({ Authorization: 'Bearer t', 'X-Tenant-URN': 'urn:x' }),
  })
  await client.list({ unreadOnly: true, before: '2026-11-12T09:00:00Z', limit: 5 })
  assert.deepEqual(await client.unreadCount(), { count: 2, urgent: true, by_level: { danger: 2 } })
  await client.markRead('n/1')
  await client.dismiss('n2')
  assert.equal(calls[0].url,
    '/api/v1/docscomms/notifications?unread_only=true&before=2026-11-12T09%3A00%3A00Z&limit=5')
  assert.equal(calls[0].headers.get('authorization'), 'Bearer t')
  assert.equal(calls[0].headers.get('x-tenant-urn'), 'urn:x')
  assert.deepEqual(calls.slice(2).map((c) => [c.method, c.url]), [
    ['POST', '/api/v1/docscomms/notifications/n%2F1/read'],
    ['POST', '/api/v1/docscomms/notifications/n2/dismiss'],
  ])
})

test('a refusal becomes a NotificationsError with the detail', async () => {
  const { impl } = fakeFetch([{ status: 404, body: { detail: 'No such notification.' } }])
  const client = createNotificationsClient({ baseUrl: '/n', fetch: impl })
  await assert.rejects(client.dismiss('x'), (e: unknown) =>
    e instanceof NotificationsError && e.status === 404 && e.message === 'No such notification.')
})

test('timeAgo reads offset-less stamps as UTC', () => {
  const now = Date.parse('2026-11-12T10:00:00Z')
  assert.equal(timeAgo('2026-11-12T09:59:50Z', now), 'just now')
  assert.equal(timeAgo('2026-11-12T09:55:00', now), '5 min ago')
  assert.equal(timeAgo('2026-11-12T07:00:00+00:00', now), '3 h ago')
  assert.equal(timeAgo('2026-11-10T10:00:00Z', now), '2 d ago')
})
