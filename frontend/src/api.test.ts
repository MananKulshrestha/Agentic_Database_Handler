import { afterEach, expect, test, vi } from 'vitest';
import { api } from './api';

afterEach(() => vi.unstubAllGlobals());

test('returns parsed API data', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ status: 'ready' }) }));
  await expect(api('/health/ready')).resolves.toEqual({ status: 'ready' });
});

test('surfaces the structured API error message', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 422, json: async () => ({ code: 'VALIDATION_ERROR', message: 'Choose a table' }) }));
  await expect(api('/runs')).rejects.toThrow('Choose a table');
});

test('does not try to parse an empty delete response', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 204 }));
  await expect(api('/connections/example', { method: 'DELETE' })).resolves.toBeUndefined();
});

test('sends the session bearer token for protected APIs', async () => {
  const fetcher = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ ok: true }) });
  vi.stubGlobal('fetch', fetcher);
  vi.stubGlobal('sessionStorage', { getItem: () => 'test-token' });
  await api('/project');
  expect((fetcher.mock.calls[0][1].headers as Headers).get('Authorization')).toBe('Bearer test-token');
});
