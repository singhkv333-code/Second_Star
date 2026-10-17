import { render, screen, fireEvent, within, act } from '@testing-library/react';
import { MarketsTab } from '@/components/markets/MarketsTab';
import { getMarketExplore } from '@/lib/markets';

vi.mock('@/lib/markets', async (original) => ({ ...await original<typeof import('@/lib/markets')>(),
  getMarketExplore: vi.fn(async () => ({ error: { code: 'auth_unavailable', message: 'Session check temporarily unavailable' } })) }));
vi.mock('@/components/markets/Overview', () => ({ BriefCard: () => null, BreadthCard: () => null, MarketStrip: () => null }));
vi.mock('@/components/markets/NewsRail', () => ({ NewsRail: () => null, EventsCalendar: () => null }));
beforeEach(() => {
  Element.prototype.scrollTo = vi.fn();
  vi.stubGlobal('IntersectionObserver', class { observe() {} disconnect() {} });
});
it('keeps asset selection separate from accessible text section navigation', async () => {
  render(<MarketsTab />);
  const sections = screen.getByRole('navigation', { name: 'Sections' });
  expect(within(sections).getByRole('button', { name: 'Overview' })).toHaveAttribute('aria-current', 'location');
  expect(within(sections).getByRole('button', { name: 'Explore' })).not.toHaveAttribute('aria-current');
  expect(await screen.findByText('Session check temporarily unavailable')).toBeVisible();
});
it('offers a real retry after a failed session check', async () => {
  render(<MarketsTab />);
  const retry = await screen.findByRole('button', { name: 'Try again' });
  const calls = vi.mocked(getMarketExplore).mock.calls.length;
  await act(async () => { fireEvent.click(retry); });
  expect(vi.mocked(getMarketExplore).mock.calls.length).toBeGreaterThan(calls);
});
