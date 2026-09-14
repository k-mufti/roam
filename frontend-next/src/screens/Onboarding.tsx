import { useState } from 'react'
import type { Budget, Path, Trip, TripType } from '../types'
import { Choice, Masthead, Ornament, Stepper } from '../components/ui'
import { recap } from '../lib/labels'

/**
 * Screen 1 — one question at a time, six steps.
 * Fully controlled by App so that leaving and coming back preserves answers.
 */

const COUNTRIES = [
  { id: 'Spain', glyph: '🇪🇸', note: 'Madrid, Seville, San Sebastián', ready: true },
  { id: 'Italy', glyph: '🇮🇹', note: 'Rome, Florence, Puglia', ready: false },
  { id: 'Japan', glyph: '🇯🇵', note: 'Tokyo, Kyoto, Kanazawa', ready: false },
  { id: 'Argentina', glyph: '🇦🇷', note: 'Buenos Aires, Mendoza', ready: false },
]

const TRIP_TYPES: { id: TripType; label: string; note: string; glyph: string }[] = [
  { id: 'family', label: 'Family', note: 'Shorter days, somewhere for everyone', glyph: '❦' },
  { id: 'friends', label: 'Friends', note: 'Late tables and later nights', glyph: '✧' },
  { id: 'couple', label: 'Couple', note: 'Quiet corners, good wine', glyph: '❧' },
  { id: 'solo', label: 'Solo', note: 'Your own pace, entirely', glyph: '✦' },
]

const BUDGETS: { id: Budget; label: string; note: string }[] = [
  { id: 'budget', label: 'Budget', note: 'Guesthouses and market food · from €90 a night' },
  { id: 'moderate', label: 'Moderate', note: 'Good small hotels, a nice dinner or two · €150–250' },
  { id: 'luxury', label: 'Luxury', note: 'The best rooms and tables in the city · €300+' },
]

const STEPS = 6

type Props = {
  trip: Trip
  patch: (fields: Partial<Trip>) => void
  step: number
  setStep: (n: number) => void
  onDone: (path: Path) => void
  onRestart: () => void
}

export default function Onboarding({ trip, patch, step, setStep, onDone, onRestart }: Props) {
  const { country, tripType, groupSize, days, budget } = trip
  const [soonFor, setSoonFor] = useState<string | null>(null)

  const setCountry = (v: string) => patch({ country: v })
  const setTripType = (v: TripType) => patch({ tripType: v })
  const setGroupSize = (v: number) => patch({ groupSize: v })
  const setDays = (v: number) => patch({ days: v })
  const setBudget = (v: Budget) => patch({ budget: v })

  const canAdvance =
    (step === 0 && country !== null) ||
    (step === 1 && tripType !== null) ||
    step === 2 ||
    step === 3 ||
    (step === 4 && budget !== null)

  const next = () => setStep(Math.min(step + 1, STEPS - 1))
  const back = () => setStep(Math.max(step - 1, 0))

  return (
    <div className="onboarding">
      <Masthead
        right={
          <div className="bar-actions">
            {(step > 0 || country !== null) && (
              <button className="btn btn-ghost" onClick={onRestart}>Start over</button>
            )}
            <span className="step-count">Step {step + 1} of {STEPS}</span>
          </div>
        }
      />
      <div className="progress-track" aria-hidden>
        {Array.from({ length: STEPS }, (_, i) => (
          <div key={i} className={`progress-tick ${i <= step ? 'is-done' : ''}`} />
        ))}
      </div>

      <main className="step-stage">
        {/* key forces the rise animation on every step change */}
        <section className="step-card" key={step}>
          {step === 0 && (
            <>
              <div className="step-head">
                <span className="eyebrow">To begin</span>
                <h1>Where are you traveling?</h1>
                <span className="lede">We are opening one country at a time, properly.</span>
              </div>
              <div className="choice-grid quad">
                {COUNTRIES.map((c) => (
                  <Choice
                    key={c.id}
                    label={c.id}
                    note={c.note}
                    glyph={c.glyph}
                    soon={!c.ready}
                    disabled={!c.ready}
                    selected={country === c.id}
                    onClick={() => {
                      if (!c.ready) { setSoonFor(c.id); return }
                      setSoonFor(null)
                      setCountry(c.id)
                    }}
                  />
                ))}
              </div>
              {soonFor && (
                <p className="step-inline-note" style={{ marginTop: '1.25rem' }}>
                  {soonFor} is not open yet — we are still walking it. Spain is ready today.
                </p>
              )}
            </>
          )}

          {step === 1 && (
            <>
              <div className="step-head">
                <span className="eyebrow">Who is going</span>
                <h1>What kind of trip is this?</h1>
                <span className="lede">It changes the pace more than the places.</span>
              </div>
              <div className="choice-grid quad">
                {TRIP_TYPES.map((t) => (
                  <Choice
                    key={t.id}
                    label={t.label}
                    note={t.note}
                    glyph={t.glyph}
                    selected={tripType === t.id}
                    onClick={() => setTripType(t.id)}
                  />
                ))}
              </div>
            </>
          )}

          {step === 2 && (
            <>
              <div className="step-head">
                <span className="eyebrow">The party</span>
                <h1>How many of you?</h1>
                <span className="lede">Including yourself.</span>
              </div>
              <Stepper value={groupSize} unit={groupSize === 1 ? 'traveller' : 'travellers'} min={1} max={12} onChange={setGroupSize} />
            </>
          )}

          {step === 3 && (
            <>
              <div className="step-head">
                <span className="eyebrow">The length</span>
                <h1>How many days in Madrid?</h1>
                <span className="lede">Three is enough. Five is better.</span>
              </div>
              <Stepper value={days} unit={days === 1 ? 'day' : 'days'} min={1} max={10} onChange={setDays} />
            </>
          )}

          {step === 4 && (
            <>
              <div className="step-head">
                <span className="eyebrow">The budget</span>
                <h1>How would you like to travel?</h1>
                <span className="lede">A tier, not a number — we will work within it.</span>
              </div>
              <div className="choice-grid">
                {BUDGETS.map((b) => (
                  <Choice
                    key={b.id}
                    label={b.label}
                    note={b.note}
                    selected={budget === b.id}
                    onClick={() => setBudget(b.id)}
                  />
                ))}
              </div>
            </>
          )}

          {step === 5 && (
            <>
              <div className="step-head">
                <span className="eyebrow">Last thing</span>
                <h1>Shall we plan it, or would you rather?</h1>
                <span className="lede">{recap(trip)}</span>
              </div>
              <div className="choice-grid two">
                <Choice
                  label="Browse curated packages"
                  note="Four itineraries we have already built and walked. Pick one and adjust it."
                  glyph="❦"
                  onClick={() => onDone('curated')}
                />
                <Choice
                  label="Build your own trip"
                  note="A few more questions, then choose your hotel and fill the days yourself."
                  glyph="✎"
                  onClick={() => onDone('custom')}
                />
              </div>
              <Ornament />
            </>
          )}

          <div className="step-foot">
            <button type="button" className="btn btn-ghost" onClick={back} disabled={step === 0}>
              ← Back
            </button>
            {step < 5 ? (
              <button type="button" className="btn btn-primary" onClick={next} disabled={!canAdvance}>
                Continue
              </button>
            ) : (
              <span className="step-inline-note">Choose a path above to continue</span>
            )}
          </div>
        </section>
      </main>
    </div>
  )
}
