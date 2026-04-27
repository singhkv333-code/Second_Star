import axios from 'axios';
import { useAuthStore } from '../store/authStore';

const BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export const client = axios.create({
  baseURL: BASE_URL,
  timeout: 30000,
});

client.interceptors.request.use((config) => {
  const token = useAuthStore.getState().accessToken;
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

client.interceptors.response.use(
  (res) => res,
  async (error) => {
    const original = error.config;
    if (error.response?.status === 401 && !original._retry) {
      original._retry = true;
      try {
        const refreshToken = useAuthStore.getState().refreshToken;
        const res = await axios.post(`${BASE_URL}/auth/refresh`, { refresh_token: refreshToken });
        useAuthStore.getState().setTokens(res.data.access_token, res.data.refresh_token);
        original.headers.Authorization = `Bearer ${res.data.access_token}`;
        return client(original);
      } catch {
        useAuthStore.getState().logout();
      }
    }
    return Promise.reject(error);
  }
);

export const SSE_URL = BASE_URL;
