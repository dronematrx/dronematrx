interface TelemetryRow {
  label: string
  value: string
}

interface TelemetryCard {
  title: string
  rows: TelemetryRow[]
}

/** Spec 6.4: stacked setting cards, uppercase title with ellipsis, one or two data rows. */
export function TelemetryPanel({ cards }: { cards: TelemetryCard[] }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--dm-space-sm)' }}>
      {cards.map((card) => (
        <div key={card.title} style={{ background: 'var(--dm-slate-700)', borderRadius: 'var(--dm-radius-md)', padding: 'var(--dm-space-md)' }}>
          <div style={{ textTransform: 'uppercase', fontSize: '.85rem', letterSpacing: '.06em', marginBottom: '.5rem', opacity: 0.85 }}>
            {card.title}&hellip;
          </div>
          <div style={{ display: 'grid', gap: '.25rem' }}>
            {card.rows.map((row) => (
              <div key={row.label} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '.85rem' }}>
                <span style={{ opacity: 0.7 }}>{row.label}</span>
                <span className="dm-mono">{row.value}</span>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
