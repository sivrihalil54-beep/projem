import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './index.css'

const rootEl = document.getElementById('root')
if (!rootEl) {
  throw new Error('Kök eleman (#root) bulunamadi; index.html kontrol edin.')
}

createRoot(rootEl).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
