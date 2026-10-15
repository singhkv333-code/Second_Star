import { render, screen, within, fireEvent, act } from '@testing-library/react';
import { BriefCard } from '@/components/markets/Overview';
import { getMarketBrief, refreshMarketBrief } from '@/lib/markets';

const snapshot = {
  text: '- **India:** Indian session summary.\n- **Global:** Global summary.',
  source: 'model' as const, as_of: null, generated_at: 1000,
  bullets: [
    { text: '**India:** Indian session summary.', source_ids: [] },
    { text: '**Global:** Global summary.', source_ids: [] },
  ],
};
vi.mock('@/lib/markets', async (original) => ({ ...await original<typeof import('@/lib/markets')>(),
  getMarketBrief: vi.fn(), refreshMarketBrief: vi.fn(),
}));
beforeEach(() => {
  vi.mocked(getMarketBrief).mockReset().mockResolvedValue({ data: snapshot });
  vi.mocked(refreshMarketBrief).mockReset();
});
it('reads the shared snapshot without triggering generation and renders real bullets/bold labels', async () => {
  render(<BriefCard data={null} />);
  expect(await screen.findByText('India:')).toHaveProperty('tagName', 'STRONG');
  expect(within(screen.getByRole('list')).getAllByRole('listitem')).toHaveLength(2);
  expect(refreshMarketBrief).not.toHaveBeenCalled();
  expect(screen.getByText(/Updated.*IST.*hourly/)).toBeVisible();
});
it('lets a user request refresh and retains the brief on failure', async () => {
  vi.mocked(refreshMarketBrief).mockResolvedValue({ error: { code: 'not_yet_available', message: 'Refresh temporarily unavailable' } });
  render(<BriefCard data={null} />);
  await screen.findByText('India:');
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Refresh market brief' })); });
  expect(refreshMarketBrief).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('status')).toHaveTextContent('Refresh temporarily unavailable');
  expect(screen.getByText('India:')).toBeVisible();
});
