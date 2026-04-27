import { create } from 'zustand';

export const usePortfolioStore = create((set) => ({
  summary: null,
  holdings: [],
  sectors: [],
  yields: [],
  activeProducts: [],
  isLoading: false,
  lastUpdated: null,

  setSummary: (summary) => set({ summary }),
  setHoldings: (holdings) => set({ holdings }),
  setSectors: (sectors) => set({ sectors }),
  setYields: (yields) => set({ yields }),
  setActiveProducts: (activeProducts) => set({ activeProducts }),
  setLoading: (isLoading) => set({ isLoading }),
  setLastUpdated: (ts) => set({ lastUpdated: ts }),
}));
