import { useState } from 'react'
import { Chip, Choice, Masthead } from '../components/ui'
import type { Pace, Trip } from '../types'

/**
 * Screen 2b — three short questions before the map, for people who chose
 * to build their own trip.
 */

const INTERESTS = [
  'Foodie', 'Museums', 'Nightlife', 'Architecture',
  'Markets', 'Parks & walking', 'Live music', 'Shopping',
]

const PACES: { id: Pace; label: string; note: string }[] = [
  { id: 'gentle', label: 'Gentle', note: 'Two things a day, long lunches' },
  { id: 'balanced', label: 'Balanced', note: 'Three or four stops, room to wander' },
  { id: 'packed', label: 'Packed', note: 'See as much as the daylight allows' },
]

const STEPS = 3

type Props = {
  trip: Trip
  patch: (fields: Partial<Trip>) => void
  step: number
  setStep: (n: number) => void
  onDone: () => void
  onBack: () => void
}

export default function Refine({ trip, patch, step, setStep, onDone, onBack }: Props) {
  /* `hasHotel` is derived: null until answered, then yes/no. */
  const [hasHotel, setHasHotel] = useState<boolean | null>(
    trip.ownHotel === null ? null : true,
  )
  const hotelName = trip.ownHotel ?? ''
  const { pace, interests } = trip

  const setHotelName = (v: string) => patch({ ownHotel: v })
  const setPace = (v: Pace) => patch({ pace: v })

  const toggle = (tag: string) =>
    patch({
      interests: interests.includes(tag)
        ? interests.filter((t) => t !== tag)
        : [...interests, tag],
    })

  const canAdvance =
    (step === 0 && (hasHotel === false || (hasHotel === true && hotelName.trim() !== ''))) ||
    (step === 1 && pace !== null) ||
    step === 2

  const finish = () => {
    patch({ ownHotel: hasHotel ? hotelName.trim() : null })
    onDone()
  }

  return (
    <div className="onboarding">
      <Masthead right={<span className="step-count">A few more · {step + 1} of {STEPS}</span>} />
      <div className="progress-track" aria-hidden>
        {Array.from({ length: STEPS }, (_, i) => (
          <div key={i} className={`progress-tick ${i <= step ? 'is-done' : ''}`} />
        ))}
      </div>

      <main className="step-stage">
        <section className="step-card" key={step}>
          {step === 0 && (
            <>
              <div className="step-head">
                <span className="eyebrow">Where you sleep</span>
                <h1>Do you already have a hotel?</h1>
                <span className="lede">Everything else is planned around it, so it matters.</span>
              </div>
              <div className="choice-grid two">
                <Choice
                  label="Yes, it is booked"
                  note="Tell us where and we will plan around that corner of the city"
                  selected={hasHotel === true}
                  onClick={() => { setHasHotel(true); patch({ ownHotel: hotelName }) }}
                />
                <Choice
                  label="No, help me choose"
                  note="We will show you four on the map and you can pick"
                  selected={hasHotel === false}
                  onClick={() => { setHasHotel(false); patch({ ownHotel: null }) }}
                />
              </div>
              {hasHotel === true && (
                <div style={{ marginTop: '1.5rem' }}>
                  <input
                    className="field"
                    placeholder="Hotel name or address…"
                    value={hotelName}
                    onChange={(e) => setHotelName(e.target.value)}
                    autoFocus
                  />
                  <p className="step-inline-note" style={{ marginTop: '0.6rem', display: 'block' }}>
                    We will still show you the map — your hotel will simply be the one already chosen.
                  </p>
                </div>
              )}
            </>
          )}

          {step === 1 && (
            <>
              <div className="step-head">
                <span className="eyebrow">The rhythm</span>
                <h1>How full should the days be?</h1>
                <span className="lede">Most people overfill them. We will gently resist.</span>
              </div>
              <div className="choice-grid">
                {PACES.map((p) => (
                  <Choice
                    key={p.id}
                    label={p.label}
                    note={p.note}
                    selected={pace === p.id}
                    onClick={() => setPace(p.id)}
                  />
                ))}
              </div>
            </>
          )}

          {step === 2 && (
            <>
              <div className="step-head">
                <span className="eyebrow">Your leanings</span>
                <h1>Anything you would hate to miss?</h1>
                <span className="lede">Pick as many as you like, or none at all.</span>
              </div>
              <div className="chip-row">
                {INTERESTS.map((tag) => (
                  <Chip key={tag} label={tag} on={interests.includes(tag)} onClick={() => toggle(tag)} />
                ))}
              </div>
            </>
          )}

          <div className="step-foot">
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => (step === 0 ? onBack() : setStep(step - 1))}
            >
              ← Back
            </button>
            <button
              type="button"
              className="btn btn-primary"
              disabled={!canAdvance}
              onClick={() => (step === STEPS - 1 ? finish() : setStep(step + 1))}
            >
              {step === STEPS - 1 ? 'To the map' : 'Continue'}
            </button>
          </div>
        </section>
      </main>
    </div>
  )
}
