import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export const useOrderStore = create(persist(
  (set, get) => ({
    pendingPreview: null,
    setPendingPreview: (preview) => set({ pendingPreview: preview }),
    clearPendingPreview: () => set({ pendingPreview: null }),

    orderHistory: [],
    setOrderHistory: (orders) => set({ orderHistory: orders }),

    executionLog: [],
    addExecution: (execution) => set((state) => ({
      executionLog: [
        {
          ...execution,
          id: Date.now(),
          executed_at: new Date().toISOString(),
        },
        ...state.executionLog,
      ].slice(0, 500),
    })),
    clearExecutionLog: () => set({ executionLog: [] }),

    gttOrders: [],
    setGTTOrders: (orders) => set({ gttOrders: orders }),
    addGTTOrder: (order) => set((state) => ({
      gttOrders: [{ ...order, created_at: new Date().toISOString() }, ...state.gttOrders],
    })),
  }),
  {
    name: 'pivot-orders',
    partialize: (s) => ({
      executionLog: s.executionLog,
      gttOrders: s.gttOrders,
    }),
  }
));
