/**
 * Stand-in for a photograph. A tinted, hatched panel with the subject's
 * initial — deliberately not a grey "image missing" box.
 */
export default function Plate({ name, tag }: { name: string; tag?: string }) {
  return (
    <div className="plate" aria-hidden>
      <span className="plate-mark">{name.replace(/^(The|El|La|Casa|Hotel)\s+/i, '').charAt(0)}</span>
      {tag && <span className="plate-tag">{tag}</span>}
    </div>
  )
}
