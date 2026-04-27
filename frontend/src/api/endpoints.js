import { client } from './client';

// Auth
export const authRegister = (data) => client.post('/auth/register', data);
export const authLogin    = (data) => client.post('/auth/login', data);

// Portfolio
export const fetchPortfolioSummary  = () => client.get('/portfolio/summary');
export const fetchHoldings          = () => client.get('/portfolio/holdings');
export const fetchSectorBreakdown   = () => client.get('/portfolio/sector');
export const fetchYields            = (tax_slab = 0.30) => client.get(`/portfolio/yields?tax_slab=${tax_slab}`);
export const fetchActiveProducts    = () => client.get('/portfolio/products');

// Orders
export const previewOrder   = (data) => client.post('/orders/preview', data);
export const confirmOrder   = (data) => client.post('/orders/confirm', data);
export const fetchOrders    = (limit = 30) => client.get(`/orders/history?limit=${limit}`);
export const createGTT      = (data) => client.post('/orders/gtt', data);
export const cancelOrder    = (orderId) => client.delete(`/orders/${orderId}`);

// Chat
export const sendChat = (messages) => client.post('/chat', { messages });

// SIP
export const fetchSIPs  = () => client.get('/sip');
export const createSIP  = (data) => client.post('/sip', data);
export const pauseSIP   = (id) => client.patch(`/sip/${id}/pause`);
export const resumeSIP  = (id) => client.patch(`/sip/${id}/resume`);
export const deleteSIP  = (id) => client.delete(`/sip/${id}`);

// Strategies
export const fetchStrategies  = () => client.get('/strategies');
export const createStrategy   = (data) => client.post('/strategies', data);
export const pauseStrategy    = (id) => client.patch(`/strategies/${id}/pause`);
export const resumeStrategy   = (id) => client.patch(`/strategies/${id}/resume`);

// Products
export const previewProduct = (data) => client.post('/products/preview', data);
export const fetchCatalogue = () => client.get('/products/catalogue');

// Backtest
export const runBacktest = (data) => client.post('/backtest/run', data);
