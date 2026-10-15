import { createRoot } from 'react-dom/client';
import App from './App.jsx';
import SessionNotices from './components/SessionNotices.jsx';
import { ConfirmProvider, ToastProvider } from './components/ui.jsx';
import './styles.css';
import { installViewport } from './viewport.js';

installViewport();

createRoot(document.getElementById('root')).render(
  <ToastProvider>
    <ConfirmProvider>
      <App />
      <SessionNotices />
    </ConfirmProvider>
  </ToastProvider>,
);
