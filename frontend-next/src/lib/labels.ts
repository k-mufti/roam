import type { Budget, Trip } from '../types'

/** One place for the phrasing, so nothing reads "1 days" or "budget budget". */

export const plural = (n: number, one: string, many = `${one}s`) =>
  `${n} ${n === 1 ? one : many}`

export const days = (n: number) => plural(n, 'day')
export const travellers = (n: number) => plural(n, 'traveller')
export const stops = (n: number) => plural(n, 'stop')
export const nights = (n: number) => plural(n, 'night')

export const BUDGET_LABEL: Record<Budget, string> = {
  budget: 'modest budget',
  moderate: 'moderate budget',
  luxury: 'luxury budget',
}

export const budgetLabel = (b: Budget | null) => (b ? BUDGET_LABEL[b] : '')

/** "4 days in Madrid · 2 travellers · moderate budget" */
export const recap = (trip: Trip) =>
  [
    `${days(trip.days)} in Madrid`,
    travellers(trip.groupSize),
    budgetLabel(trip.budget),
  ]
    .filter(Boolean)
    .join(' · ')
