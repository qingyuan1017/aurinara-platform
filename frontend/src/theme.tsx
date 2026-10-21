import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Check, Moon, Sun, SunMoon } from 'lucide-react'

export type ThemeMode = 'light' | 'dark' | 'system'
export type ResolvedTheme = Exclude<ThemeMode, 'system'>

const STORAGE_KEY = 'aurinara-platform.ui.theme'

function isThemeMode(value: string | null): value is ThemeMode {
  return value === 'light' || value === 'dark' || value === 'system'
}

export function resolveTheme(mode: ThemeMode, prefersDark = false): ResolvedTheme {
  return mode === 'system' ? (prefersDark ? 'dark' : 'light') : mode
}

type ThemeContextValue = {
  mode: ThemeMode
  resolvedTheme: ResolvedTheme
  setMode: (mode: ThemeMode) => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<ThemeMode>(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY)
      return isThemeMode(stored) ? stored : 'system'
    } catch {
      return 'system'
    }
  })
  const [systemDark, setSystemDark] = useState(() => window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false)
  const resolvedTheme = resolveTheme(mode, systemDark)

  useEffect(() => {
    const query = window.matchMedia?.('(prefers-color-scheme: dark)')
    const update = () => setSystemDark(query?.matches ?? false)
    query?.addEventListener?.('change', update)
    return () => query?.removeEventListener?.('change', update)
  }, [])

  useEffect(() => {
    const root = document.documentElement
    root.classList.toggle('dark', resolvedTheme === 'dark')
    root.classList.toggle('light', resolvedTheme === 'light')
    root.dataset.theme = resolvedTheme
    root.dataset.themeMode = mode
    try { localStorage.setItem(STORAGE_KEY, mode) } catch { /* storage is optional */ }
  }, [mode, resolvedTheme])

  const value = useMemo(() => ({ mode, resolvedTheme, setMode }), [mode, resolvedTheme])
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme() {
  const context = useContext(ThemeContext)
  if (!context) throw new Error('useTheme must be used within ThemeProvider')
  return context
}

const options: { mode: ThemeMode; label: string; icon: typeof Sun }[] = [
  { mode: 'light', label: 'Light', icon: Sun },
  { mode: 'dark', label: 'Dark', icon: Moon },
  { mode: 'system', label: 'System', icon: SunMoon },
]

export function ThemeToggle() {
  const { mode, setMode } = useTheme()
  const [open, setOpen] = useState(false)
  return <div className="theme-control">
    <button className="icon ghost" aria-label={`Theme: ${mode}`} title="Change theme" aria-expanded={open} onClick={() => setOpen((value) => !value)}><SunMoon /></button>
    {open && <div className="theme-menu" role="menu" aria-label="Theme">
      {options.map(({ mode: value, label, icon: Icon }) => <button key={value} role="menuitemradio" aria-checked={mode === value} onClick={() => { setMode(value); setOpen(false) }}><Icon /><span>{label}</span>{mode === value && <Check />}</button>)}
    </div>}
  </div>
}
