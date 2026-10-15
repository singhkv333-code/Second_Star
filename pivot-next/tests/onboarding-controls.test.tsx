import { render, screen, fireEvent } from '@testing-library/react';
import { Welcome } from '@/components/onboarding/Welcome';

vi.mock('@/lib/api', () => ({ getMe: async () => ({ error: 'guest' }), getCompanyLogos: async () => ({}) }));
vi.mock('@/lib/markets', () => ({ getMarketExplore: async () => ({ error: 'unavailable' }) }));
vi.mock('@/lib/instruments', () => ({ loadUniverse: async () => new Map(), searchInstruments: async () => [],
  fallback: (symbol: string) => ({ symbol, title: symbol, sub: 'NSE', kind: 'stock' }) }));
vi.mock('@/lib/watchlists', () => ({ useWatchlists: () => ({ lists: [] }), setWatchlist: vi.fn() }));
vi.mock('@/components/CompanyLogo', () => ({ CompanyLogo: () => null }));
vi.mock('@/components/brand/PivotWordLogo', () => ({ PivotWordLogo: () => null }));

beforeEach(() => {
  Element.prototype.scrollTo = vi.fn();
});
it('dismisses with an accessible icon-only close button', async () => {
  const onDone = vi.fn();
  render(<Welcome onDone={onDone} />);
  const close = await screen.findByRole('button', { name: 'Close introduction' });
  expect(close.textContent).toBe('');
  fireEvent.click(close);
  expect(onDone).toHaveBeenCalledWith(false);
});
it('has arrow-free navigation and descriptive appearance icons', async () => {
  render(<Welcome onDone={vi.fn()} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Get started' }));
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
  expect(screen.getByRole('button', { name: 'Back' }).querySelector('svg')).toBeNull();
  expect(screen.queryByRole('button', { name: /Skip/ })).toBeNull();
  for (const name of ['Light', 'Dark', 'Custom']) {
    expect(screen.getByRole('radio', { name }).querySelector('svg')).not.toBeNull();
  }
  fireEvent.click(screen.getByRole('radio', { name: 'Dark' }));
  expect(screen.getByRole('radio', { name: 'Dark' })).toHaveAttribute('aria-checked', 'true');
});
