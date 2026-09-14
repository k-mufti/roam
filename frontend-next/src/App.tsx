import { useEffect, useState } from 'react'
import Onboarding from './screens/Onboarding'
import Packages from './screens/Packages'
import Refine from './screens/Refine'
import Hotels from './screens/Hotels'
import Planner from './screens/Planner'
import Summary from './screens/Summary'
import type { Package } from './fixtures/madrid'
import { clearSession, loadSession, saveSession } from './lib/storage'
import { emptyTrip, type Path, type Screen, type Trip } from './types'

export default function App() {
  /* One read at startup; everything after is kept in sync by the effect below. */
  const [session] = useState(loadSession)

  const [screen, setScreen] = useState<Screen>(session.screen)
  const [trip, setTrip] = useState<Trip>(session.trip)

  /* Step indices live here so going Back into a flow resumes where you left. */
  const [onboardStep, setOnboardStep] = useState(session.onboardStep)
  const [refineStep, setRefineStep] = useState(session.refineStep)

  useEffect(() => {
    saveSession({ trip, screen, onboardStep, refineStep })
  }, [trip, screen, onboardStep, refineStep])

  const patch = (fields: Partial<Trip>) => setTrip((t) => ({ ...t, ...fields }))

  const choosePath = (path: Path) => {
    patch({ path })
    setScreen(path === 'curated' ? 'packages' : 'refine')
  }

  /** Loading a package = adopt its hotel and deal its stops across the days. */
  const pickPackage = (pkg: Package) => {
    const days = trip.days || pkg.days
    const itinerary: Record<number, string[]> = {}
    pkg.placeIds.forEach((id, i) => {
      const day = i % days
      itinerary[day] = [...(itinerary[day] ?? []), id]
    })
    patch({ hotelId: pkg.hotelId, itinerary })
    setScreen('planner')
  }

  const restart = () => {
    clearSession()
    setTrip(emptyTrip)
    setOnboardStep(0)
    setRefineStep(0)
    setScreen('onboarding')
  }

  switch (screen) {
    case 'onboarding':
      return (
        <Onboarding
          trip={trip}
          patch={patch}
          step={onboardStep}
          setStep={setOnboardStep}
          onDone={choosePath}
          onRestart={restart}
        />
      )

    case 'packages':
      return <Packages trip={trip} onPick={pickPackage} onBack={() => setScreen('onboarding')} />

    case 'refine':
      return (
        <Refine
          trip={trip}
          patch={patch}
          step={refineStep}
          setStep={setRefineStep}
          onBack={() => setScreen('onboarding')}
          onDone={() => setScreen('hotels')}
        />
      )

    case 'hotels':
      return (
        <Hotels
          trip={trip}
          onBack={() => setScreen('refine')}
          onSelect={(hotelId) => {
            patch({ hotelId })
            setScreen('planner')
          }}
        />
      )

    case 'planner':
      return (
        <Planner
          trip={trip}
          setItinerary={(update) => setTrip((t) => ({ ...t, itinerary: update(t.itinerary) }))}
          onBack={() => setScreen(trip.path === 'curated' ? 'packages' : 'hotels')}
          onFinish={() => setScreen('summary')}
        />
      )

    case 'summary':
      return <Summary trip={trip} onBack={() => setScreen('planner')} onRestart={restart} />
  }
}
