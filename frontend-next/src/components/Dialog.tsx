import { useEffect, useRef, type ReactNode } from 'react'

/**
 * Shared behaviour for the hotel drawer and the itinerary modal:
 * Escape closes, focus moves in and comes back out, Tab stays inside,
 * and the page behind stops scrolling.
 */
export default function Dialog({
  label,
  onClose,
  className = 'drawer',
  children,
}: {
  label: string
  onClose: () => void
  className?: string
  children: ReactNode
}) {
  const panel = useRef<HTMLDivElement>(null)
  const restoreTo = useRef<HTMLElement | null>(null)

  useEffect(() => {
    restoreTo.current = document.activeElement as HTMLElement | null

    const { overflow } = document.body.style
    document.body.style.overflow = 'hidden'

    /* Move focus to the first thing worth focusing inside the panel. */
    const focusables = () =>
      Array.from(
        panel.current?.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        ) ?? [],
      ).filter((el) => !el.hasAttribute('disabled'))

    focusables()[0]?.focus()

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onClose()
        return
      }
      if (e.key !== 'Tab') return

      const items = focusables()
      if (items.length === 0) return
      const first = items[0]
      const last = items[items.length - 1]

      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = overflow
      restoreTo.current?.focus?.()
    }
  }, [onClose])

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <div className={className} role="dialog" aria-modal="true" aria-label={label} ref={panel}>
        {children}
      </div>
    </>
  )
}
