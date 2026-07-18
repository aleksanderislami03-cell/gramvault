import { NavLink } from 'react-router-dom'

const LINKS = [
  { to: '/', label: 'Gallery', end: true },
  { to: '/chat', label: 'Chat', end: false },
  { to: '/import', label: 'Import', end: false },
  { to: '/settings', label: 'Settings', end: false },
]

export function NavBar() {
  return (
    <header className="sticky top-0 z-20 border-b border-surface-border bg-surface/95 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center gap-6 px-4 py-3">
        <NavLink to="/" className="flex items-center gap-2 text-slate-100 no-underline">
          <span className="text-lg font-semibold tracking-tight">GramVault</span>
        </NavLink>
        <nav className="flex items-center gap-1">
          {LINKS.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.end}
              className={({ isActive }) =>
                `rounded-lg px-3 py-1.5 text-sm font-medium no-underline transition-colors ${
                  isActive
                    ? 'bg-surface-overlay text-slate-100'
                    : 'text-slate-400 hover:bg-surface-raised hover:text-slate-200'
                }`
              }
            >
              {link.label}
            </NavLink>
          ))}
        </nav>
      </div>
    </header>
  )
}
