import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export const useAuthStore = create(persist(
  (set, get) => ({
    user: null,
    accessToken: null,
    refreshToken: null,
    isAuthenticated: false,

    login: (userData, accessToken, refreshToken) => set({
      user: userData,
      accessToken,
      refreshToken,
      isAuthenticated: true,
    }),

    logout: () => set({
      user: null,
      accessToken: null,
      refreshToken: null,
      isAuthenticated: false,
    }),

    setTokens: (accessToken, refreshToken) => set({ accessToken, refreshToken }),
  }),
  {
    name: 'pivot-auth',
    partialize: (s) => ({
      user: s.user,
      accessToken: s.accessToken,
      refreshToken: s.refreshToken,
      isAuthenticated: s.isAuthenticated,
    }),
  }
));
