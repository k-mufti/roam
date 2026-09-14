export type TripType = 'family' | 'friends' | 'couple' | 'solo'
export type Budget = 'budget' | 'moderate' | 'luxury'
export type Path = 'curated' | 'custom'
export type Pace = 'gentle' | 'balanced' | 'packed'

export type Trip = {
  country: string | null
  tripType: TripType | null
  groupSize: number
  days: number
  budget: Budget | null
  path: Path | null

  /* build-your-own answers */
  ownHotel: string | null      // null = wants help choosing
  pace: Pace | null
  interests: string[]

  /* selections */
  hotelId: string | null
  /** dayIndex (0-based) -> ordered place ids */
  itinerary: Record<number, string[]>
}

export const emptyTrip: Trip = {
  country: null,
  tripType: null,
  groupSize: 2,
  days: 4,
  budget: null,
  path: null,
  ownHotel: null,
  pace: null,
  interests: [],
  hotelId: null,
  itinerary: {},
}

export type Screen =
  | 'onboarding'
  | 'packages'
  | 'refine'
  | 'hotels'
  | 'planner'
  | 'summary'
