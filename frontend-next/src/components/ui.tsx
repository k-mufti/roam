import type { ReactNode } from 'react'

export function Ornament() {
  return (
    <div className="ornament" aria-hidden>
      <span>◆</span>
    </div>
  )
}

export function Eyebrow({ children }: { children: ReactNode }) {
  return <span className="eyebrow">{children}</span>
}

type ChoiceProps = {
  label: string
  note?: string
  glyph?: string
  selected?: boolean
  disabled?: boolean
  soon?: boolean
  onClick?: () => void
}

export function Choice({ label, note, glyph, selected, disabled, soon, onClick }: ChoiceProps) {
  return (
    <button
      type="button"
      className={[
        'choice',
        selected ? 'is-selected' : '',
        disabled ? 'is-disabled' : '',
      ].join(' ').trim()}
      aria-pressed={!!selected}
      onClick={onClick}
    >
      {soon && <span className="soon-badge">Coming soon</span>}
      {glyph && <span className="choice-flag">{glyph}</span>}
      <span className="choice-label">{label}</span>
      {note && <span className="choice-note">{note}</span>}
    </button>
  )
}

export function Chip({
  label,
  on,
  onClick,
}: { label: string; on: boolean; onClick: () => void }) {
  return (
    <button type="button" className={`chip ${on ? 'is-on' : ''}`} aria-pressed={on} onClick={onClick}>
      {label}
    </button>
  )
}

export function Stepper({
  value,
  unit,
  min,
  max,
  onChange,
}: {
  value: number
  unit: string
  min: number
  max: number
  onChange: (n: number) => void
}) {
  return (
    <div className="stepper">
      <button type="button" onClick={() => onChange(value - 1)} disabled={value <= min} aria-label={`Fewer ${unit}`}>
        –
      </button>
      <div className="stepper-value">
        <strong>{value}</strong>
        <span>{unit}</span>
      </div>
      <button type="button" onClick={() => onChange(value + 1)} disabled={value >= max} aria-label={`More ${unit}`}>
        +
      </button>
    </div>
  )
}

export function Masthead({ right }: { right?: ReactNode }) {
  return (
    <header className="masthead">
      <div>
        <span className="wordmark">
          Roam
          <span className="wordmark-sub">Curated journeys</span>
        </span>
      </div>
      {right}
    </header>
  )
}
