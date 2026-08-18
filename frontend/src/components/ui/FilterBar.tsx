import type { ReactNode } from 'react'

/** Spec 5.4: filter controls sit in a single horizontal row, pill styling, above the table they affect. */
export function FilterBar({ children }: { children: ReactNode }) {
  return (
    <div style={{ display: 'flex', gap: 'var(--dm-space-sm)', flexWrap: 'wrap', marginBottom: 'var(--dm-space-md)' }}>
      {children}
    </div>
  )
}

interface FilterFieldProps {
  label: string
  value: string
  onChange: (value: string) => void
  options?: string[]
  placeholder?: string
}

export function FilterField({ label, value, onChange, options, placeholder }: FilterFieldProps) {
  const baseStyle: React.CSSProperties = {
    border: 0,
    borderRadius: 'var(--dm-radius-pill)',
    padding: '.55rem 1.1rem',
    background: 'var(--dm-surface-inset)',
    color: 'var(--dm-text-on-dark)',
    fontSize: '.85rem',
  }

  if (options) {
    return (
      <select aria-label={label} value={value} onChange={(e) => onChange(e.target.value)} style={baseStyle}>
        <option value="">{label}&hellip;</option>
        {options.map((opt) => (
          <option key={opt} value={opt}>
            {opt}
          </option>
        ))}
      </select>
    )
  }

  return (
    <input
      aria-label={label}
      value={value}
      placeholder={placeholder ?? `${label}...`}
      onChange={(e) => onChange(e.target.value)}
      style={{ ...baseStyle, minWidth: '14rem' }}
    />
  )
}
