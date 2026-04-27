import { useEffect, useCallback } from 'react';
import { usePortfolioStore } from '../store/portfolioStore';
import {
  fetchPortfolioSummary,
  fetchHoldings,
  fetchSectorBreakdown,
  fetchYields,
  fetchActiveProducts,
} from '../api/endpoints';

export function usePortfolio(autoRefresh = true) {
  const { setSummary, setHoldings, setSectors, setYields, setActiveProducts,
          setLoading, setLastUpdated } = usePortfolioStore();

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [summary, holdings, sectors, yields, products] = await Promise.allSettled([
        fetchPortfolioSummary(),
        fetchHoldings(),
        fetchSectorBreakdown(),
        fetchYields(),
        fetchActiveProducts(),
      ]);
      if (summary.status === 'fulfilled')  setSummary(summary.value.data);
      if (holdings.status === 'fulfilled') setHoldings(holdings.value.data);
      if (sectors.status === 'fulfilled')  setSectors(sectors.value.data?.sectors || []);
      if (yields.status === 'fulfilled')   setYields(yields.value.data);
      if (products.status === 'fulfilled') setActiveProducts(products.value.data);
      setLastUpdated(new Date().toISOString());
    } catch (e) {
      console.error('Portfolio refresh failed:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    if (!autoRefresh) return;
    const interval = setInterval(refresh, 30000);
    return () => clearInterval(interval);
  }, [refresh, autoRefresh]);

  return { refresh };
}
