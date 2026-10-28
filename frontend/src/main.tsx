import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import AuthGate from './AuthGate';
import './style.css';

const client = new QueryClient({ defaultOptions: { queries: { staleTime: 20_000, retry: 1 } } });
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><QueryClientProvider client={client}><AuthGate /></QueryClientProvider></React.StrictMode>,
);
