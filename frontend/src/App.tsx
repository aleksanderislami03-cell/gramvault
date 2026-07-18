import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { NavBar } from './components/NavBar'
import { Chat } from './pages/Chat'
import { Gallery } from './pages/Gallery'
import { Import } from './pages/Import'
import { ItemDetail } from './pages/ItemDetail'
import { Settings } from './pages/Settings'

function NotFound() {
  return <p className="mx-auto max-w-3xl px-4 py-12 text-center text-sm text-slate-500">Page not found.</p>
}

function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-surface text-slate-100">
        <NavBar />
        <main>
          <Routes>
            <Route path="/" element={<Gallery />} />
            <Route path="/items/:id" element={<ItemDetail />} />
            <Route path="/chat" element={<Chat />} />
            <Route path="/import" element={<Import />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}

export default App
