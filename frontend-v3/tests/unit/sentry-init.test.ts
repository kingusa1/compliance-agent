import { describe, it, expect, vi, beforeEach } from 'vitest';

describe('sentry client config', () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it('does not throw when NEXT_PUBLIC_SENTRY_DSN is unset', async () => {
    vi.stubEnv('NEXT_PUBLIC_SENTRY_DSN', '');
    await expect(import('../../sentry.client.config')).resolves.not.toThrow();
  });

  it('does not throw when NEXT_PUBLIC_SENTRY_DSN is set', async () => {
    vi.stubEnv('NEXT_PUBLIC_SENTRY_DSN', 'https://public@example.com/1');
    await expect(import('../../sentry.client.config')).resolves.not.toThrow();
  });
});

describe('sentry server config', () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it('does not throw when SENTRY_DSN is unset', async () => {
    vi.stubEnv('SENTRY_DSN', '');
    await expect(import('../../sentry.server.config')).resolves.not.toThrow();
  });

  it('does not throw when SENTRY_DSN is set', async () => {
    vi.stubEnv('SENTRY_DSN', 'https://public@example.com/2');
    await expect(import('../../sentry.server.config')).resolves.not.toThrow();
  });
});

describe('sentry edge config', () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it('does not throw when SENTRY_DSN is unset', async () => {
    vi.stubEnv('SENTRY_DSN', '');
    await expect(import('../../sentry.edge.config')).resolves.not.toThrow();
  });

  it('does not throw when SENTRY_DSN is set', async () => {
    vi.stubEnv('SENTRY_DSN', 'https://public@example.com/3');
    await expect(import('../../sentry.edge.config')).resolves.not.toThrow();
  });
});
