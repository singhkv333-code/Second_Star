import { useCallback, useEffect, useState } from 'react';
import {
  fetchKiteStatus,
  fetchKiteLoginUrl,
  connectKiteMock,
  disconnectKite,
} from '../api/endpoints';

const initialStatus = {
  connected: false,
  mock_mode: false,
  kite_user_id: null,
  login_time: null,
};

export function useKite() {
  const [status, setStatus] = useState(initialStatus);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetchKiteStatus();
      setStatus(res.data);
      setError(null);
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || 'Failed to load Kite status');
    } finally {
      setLoading(false);
    }
  }, []);

  const connect = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetchKiteLoginUrl();
      if (res.data.mock_mode) {
        const mock = await connectKiteMock();
        setStatus(mock.data);
      } else if (res.data.login_url) {
        window.location.href = res.data.login_url;
      } else {
        setError('Kite login URL was empty');
      }
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || 'Failed to start Kite login');
    } finally {
      setBusy(false);
    }
  }, []);

  const disconnect = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await disconnectKite();
      setStatus({ ...initialStatus, mock_mode: res.data?.mock_mode ?? false });
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || 'Failed to disconnect');
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { status, loading, busy, error, refresh, connect, disconnect };
}
