import { render, screen } from '@testing-library/react';
import { AssetBoard } from '@/components/markets/AssetBoard';
import { TooltipProvider } from '@/components/ui/tooltip';

vi.mock('@/lib/instruments', () => ({ loadUniverse: async () => new Map() }));
vi.mock('@/lib/markets', async (original) => ({ ...await original<typeof import('@/lib/markets')>(),
  fetchAssetQuotes: async (symbols: string[]) => new Map(symbols.map(symbol => [symbol, { symbol, last: 74315.61, high: 75000, low: 73000, change_pct: 2.11 }])),
  fetchDailyCloses: async () => [],
}));
it('renders pairs of cards without decorative instrument or priced counts', async () => {
  render(<TooltipProvider><AssetBoard kind="indices" columns={2} /></TooltipProvider>);
  const card = await screen.findByRole('button', { name: /Sensex, open in chart/i });
  expect(card.parentElement).toHaveStyle({ gridTemplateColumns: 'repeat(2, minmax(0, 1fr))' });
  expect(await screen.findAllByText('74,315.61')).not.toHaveLength(0);
  expect(screen.queryByText(/\d+ instruments?/)).toBeNull();
  expect(screen.queryByText(/\d+ of \d+ priced/)).toBeNull();
});
