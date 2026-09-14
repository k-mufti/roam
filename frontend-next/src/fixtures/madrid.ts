/**
 * Hand-written fake data for the Madrid skeleton.
 * Nothing here comes from a real source — but the coordinates are genuine
 * Madrid locations (Salamanca, Chueca, La Latina, Retiro, Chamberí, Lavapiés)
 * so the map reads as legitimate.
 */

export type Coords = { lat: number; lng: number }

export type Hotel = {
  id: string
  name: string
  neighborhood: string
  pricePerNight: number
  stars: number
  blurb: string
  coords: Coords
  reviews: { author: string; text: string }[]
}

export type PlaceKind = 'food' | 'activity'

export type Place = {
  id: string
  name: string
  kind: PlaceKind
  category: string
  neighborhood: string
  priceLevel: '€' | '€€' | '€€€' | '€€€€'
  blurb: string
  coords: Coords
  durationMins: number
}

export type Package = {
  id: string
  name: string
  tagline: string
  description: string
  days: number
  budget: 'budget' | 'moderate' | 'luxury'
  priceRange: string
  hotelId: string
  placeIds: string[]
}

export const CITY: { name: string; country: string; center: Coords } = {
  name: 'Madrid',
  country: 'Spain',
  center: { lat: 40.4185, lng: -3.7035 },
}

export const HOTELS: Hotel[] = [
  {
    id: 'h-velarde',
    name: 'Casa Velarde',
    neighborhood: 'Salamanca',
    pricePerNight: 340,
    stars: 5,
    blurb: 'A restored 1890s townhouse on a quiet Salamanca corner, with a walled garden bar.',
    coords: { lat: 40.4288, lng: -3.6842 },
    reviews: [
      { author: 'Imogen R.', text: 'The kind of hotel where the concierge remembers your coffee order by day two.' },
      { author: 'Tomás L.', text: 'Rooms are hushed and enormous. The garden at dusk is the whole reason to book.' },
      { author: 'Anneke V.', text: 'Slightly formal, wonderfully so. Breakfast is worth waking for.' },
    ],
  },
  {
    id: 'h-almendro',
    name: 'Hotel Almendro',
    neighborhood: 'La Latina',
    pricePerNight: 185,
    stars: 4,
    blurb: 'Tiled floors, shuttered windows and a rooftop that looks straight at San Francisco el Grande.',
    coords: { lat: 40.4113, lng: -3.7098 },
    reviews: [
      { author: 'Gil M.', text: 'Right in the thick of the Sunday market crowds — exactly what we wanted.' },
      { author: 'Paula D.', text: 'Small rooms, huge charm. The rooftop is never crowded before eight.' },
    ],
  },
  {
    id: 'h-orfila',
    name: 'The Orfila Rooms',
    neighborhood: 'Chamberí',
    pricePerNight: 265,
    stars: 5,
    blurb: 'Fourteen rooms above a leafy residential street; a library bar and no lobby music.',
    coords: { lat: 40.4302, lng: -3.6959 },
    reviews: [
      { author: 'Beatriz S.', text: 'Feels like staying in a well-read friend’s enormous flat.' },
      { author: 'Henry O.', text: 'Ten minutes from everything, but you sleep like it is the countryside.' },
      { author: 'Leila K.', text: 'The library bar pours a proper vermouth.' },
    ],
  },
  {
    id: 'h-corredera',
    name: 'Corredera Guesthouse',
    neighborhood: 'Chueca',
    pricePerNight: 128,
    stars: 3,
    blurb: 'Plain, bright and cheerful, a minute from the Mercado de San Antón.',
    coords: { lat: 40.4227, lng: -3.6972 },
    reviews: [
      { author: 'Samir A.', text: 'Unfussy and spotless. You are paying for the location and it is worth it.' },
      { author: 'Noor H.', text: 'Thin walls, but the neighbourhood more than makes up for it.' },
    ],
  },
]

export const PLACES: Place[] = [
  /* ---------------- Food & cafés ---------------- */
  {
    id: 'p-bodega-lucia',
    name: 'Bodega Lucía',
    kind: 'food',
    category: 'Tapas bar',
    neighborhood: 'La Latina',
    priceLevel: '€€',
    blurb: 'Barrels for tables, anchovies on toast, vermouth poured without ceremony.',
    coords: { lat: 40.4128, lng: -3.7082 },
    durationMins: 75,
  },
  {
    id: 'p-casa-pilar',
    name: 'Casa Pilar',
    kind: 'food',
    category: 'Castilian',
    neighborhood: 'Centro',
    priceLevel: '€€€',
    blurb: 'Roast lamb, white tablecloths, waiters who have worked here for decades.',
    coords: { lat: 40.4158, lng: -3.7071 },
    durationMins: 110,
  },
  {
    id: 'p-cafe-teresa',
    name: 'Café Teresa',
    kind: 'food',
    category: 'Café',
    neighborhood: 'Chueca',
    priceLevel: '€',
    blurb: 'Marble counter, thick chocolate, the best place in Madrid to read a newspaper.',
    coords: { lat: 40.4242, lng: -3.6961 },
    durationMins: 40,
  },
  {
    id: 'p-mercado-anton',
    name: 'Mercado de San Antón',
    kind: 'food',
    category: 'Food market',
    neighborhood: 'Chueca',
    priceLevel: '€€',
    blurb: 'Three floors of stalls; graze on the second, drink on the roof.',
    coords: { lat: 40.4222, lng: -3.6957 },
    durationMins: 65,
  },
  {
    id: 'p-el-quinto',
    name: 'El Quinto Sol',
    kind: 'food',
    category: 'Modern Spanish',
    neighborhood: 'Salamanca',
    priceLevel: '€€€€',
    blurb: 'A tasting menu that takes its time. Book weeks out, dress properly.',
    coords: { lat: 40.4275, lng: -3.6811 },
    durationMins: 150,
  },
  {
    id: 'p-churreria-paz',
    name: 'Churrería La Paz',
    kind: 'food',
    category: 'Churros',
    neighborhood: 'Centro',
    priceLevel: '€',
    blurb: 'Open since before anyone can remember. Go at midnight or at eight.',
    coords: { lat: 40.4176, lng: -3.7052 },
    durationMins: 30,
  },
  {
    id: 'p-taberna-nueve',
    name: 'Taberna Nueve',
    kind: 'food',
    category: 'Tapas bar',
    neighborhood: 'Lavapiés',
    priceLevel: '€',
    blurb: 'Standing room only, patatas bravas that justify the elbowing.',
    coords: { lat: 40.4089, lng: -3.7007 },
    durationMins: 55,
  },
  {
    id: 'p-jardin-olivo',
    name: 'Jardín del Olivo',
    kind: 'food',
    category: 'Garden restaurant',
    neighborhood: 'Retiro',
    priceLevel: '€€€',
    blurb: 'Lunch under fig trees at the edge of the park; long, slow, shaded.',
    coords: { lat: 40.4147, lng: -3.6797 },
    durationMins: 120,
  },
  {
    id: 'p-vinos-almudena',
    name: 'Vinos Almudena',
    kind: 'food',
    category: 'Wine bar',
    neighborhood: 'Chamberí',
    priceLevel: '€€',
    blurb: 'Forty Spanish wines by the glass and a short, serious cheese list.',
    coords: { lat: 40.4331, lng: -3.6994 },
    durationMins: 80,
  },
  {
    id: 'p-panaderia-sol',
    name: 'Panadería del Sol',
    kind: 'food',
    category: 'Bakery',
    neighborhood: 'Malasaña',
    priceLevel: '€',
    blurb: 'Queue out the door by nine. Take the almond ensaimada.',
    coords: { lat: 40.4258, lng: -3.7051 },
    durationMins: 25,
  },

  /* ---------------- Attractions & activities ---------------- */
  {
    id: 'a-prado',
    name: 'Museo del Prado',
    kind: 'activity',
    category: 'Museum',
    neighborhood: 'Retiro',
    priceLevel: '€€',
    blurb: 'Velázquez, Goya, and more Bosch than you are ready for. Give it a morning.',
    coords: { lat: 40.4138, lng: -3.6921 },
    durationMins: 180,
  },
  {
    id: 'a-retiro',
    name: 'Parque del Retiro',
    kind: 'activity',
    category: 'Park',
    neighborhood: 'Retiro',
    priceLevel: '€',
    blurb: 'Row a boat, find the glass palace, lose an afternoon without trying.',
    coords: { lat: 40.4153, lng: -3.6838 },
    durationMins: 120,
  },
  {
    id: 'a-palacio',
    name: 'Palacio Real',
    kind: 'activity',
    category: 'Palace',
    neighborhood: 'Centro',
    priceLevel: '€€',
    blurb: 'Three thousand rooms, of which you will see forty. The armoury is the highlight.',
    coords: { lat: 40.4180, lng: -3.7143 },
    durationMins: 150,
  },
  {
    id: 'a-reina-sofia',
    name: 'Reina Sofía',
    kind: 'activity',
    category: 'Museum',
    neighborhood: 'Lavapiés',
    priceLevel: '€€',
    blurb: 'Guernica is here, and the crowd around it thins considerably after six.',
    coords: { lat: 40.4079, lng: -3.6945 },
    durationMins: 140,
  },
  {
    id: 'a-rastro',
    name: 'El Rastro',
    kind: 'activity',
    category: 'Flea market',
    neighborhood: 'La Latina',
    priceLevel: '€',
    blurb: 'Sunday mornings only. Brass fittings, old postcards, a great deal of nonsense.',
    coords: { lat: 40.4074, lng: -3.7075 },
    durationMins: 100,
  },
  {
    id: 'a-sorolla',
    name: 'Museo Sorolla',
    kind: 'activity',
    category: 'House museum',
    neighborhood: 'Chamberí',
    priceLevel: '€',
    blurb: 'The painter’s own house and garden, kept exactly as he left it. Quiet, small, perfect.',
    coords: { lat: 40.4353, lng: -3.6935 },
    durationMins: 90,
  },
  {
    id: 'a-templo-debod',
    name: 'Templo de Debod',
    kind: 'activity',
    category: 'Monument',
    neighborhood: 'Moncloa',
    priceLevel: '€',
    blurb: 'An Egyptian temple rebuilt stone by stone. Come for the sunset behind it.',
    coords: { lat: 40.4240, lng: -3.7178 },
    durationMins: 60,
  },
  {
    id: 'a-flamenco-sombra',
    name: 'Tablao La Sombra',
    kind: 'activity',
    category: 'Flamenco',
    neighborhood: 'Lavapiés',
    priceLevel: '€€€',
    blurb: 'Forty seats, no microphones, two shows a night. The late one is better.',
    coords: { lat: 40.4103, lng: -3.7024 },
    durationMins: 90,
  },
  {
    id: 'a-thyssen',
    name: 'Thyssen-Bornemisza',
    kind: 'activity',
    category: 'Museum',
    neighborhood: 'Centro',
    priceLevel: '€€',
    blurb: 'One family’s collection, hung chronologically. The most walkable museum in the city.',
    coords: { lat: 40.4160, lng: -3.6948 },
    durationMins: 120,
  },
  {
    id: 'a-mercado-cebada',
    name: 'Cebada Rooftop Walk',
    kind: 'activity',
    category: 'Viewpoint',
    neighborhood: 'La Latina',
    priceLevel: '€',
    blurb: 'A short climb for the best rooftop view south over the old town.',
    coords: { lat: 40.4119, lng: -3.7112 },
    durationMins: 45,
  },
]

export const PACKAGES: Package[] = [
  {
    id: 'pk-old-madrid',
    name: 'Old Madrid, Slowly',
    tagline: 'Five days · Moderate',
    description:
      'The historic core on foot, with long lunches and no more than one museum a day. Built for people who would rather sit in a square than tick a list.',
    days: 5,
    budget: 'moderate',
    priceRange: '€1,400 – €1,800 for two',
    hotelId: 'h-almendro',
    placeIds: [
      'a-palacio', 'p-bodega-lucia', 'a-rastro', 'p-churreria-paz',
      'a-mercado-cebada', 'p-casa-pilar', 'a-prado',
    ],
  },
  {
    id: 'pk-galleries',
    name: 'The Golden Triangle',
    tagline: 'Four days · Luxury',
    description:
      'Prado, Thyssen and Reina Sofía at an unhurried pace, bookended by the best table in Salamanca and a garden lunch beside the Retiro.',
    days: 4,
    budget: 'luxury',
    priceRange: '€2,900 – €3,600 for two',
    hotelId: 'h-velarde',
    placeIds: ['a-prado', 'a-thyssen', 'a-reina-sofia', 'p-el-quinto', 'p-jardin-olivo', 'a-retiro'],
  },
  {
    id: 'pk-market-table',
    name: 'At the Market Table',
    tagline: 'Three days · Moderate',
    description:
      'A short eating trip: markets in the morning, vermouth at noon, tapas standing up at night. Very little walking between meals.',
    days: 3,
    budget: 'moderate',
    priceRange: '€900 – €1,200 for two',
    hotelId: 'h-corredera',
    placeIds: ['p-mercado-anton', 'p-cafe-teresa', 'p-vinos-almudena', 'p-taberna-nueve', 'p-panaderia-sol', 'a-flamenco-sombra'],
  },
  {
    id: 'pk-quiet-corners',
    name: 'Quiet Corners',
    tagline: 'Five days · Budget',
    description:
      'The city away from the crowds — small house museums, residential Chamberí, parks in the late afternoon. Cheap, leafy and calm.',
    days: 5,
    budget: 'budget',
    priceRange: '€700 – €950 for two',
    hotelId: 'h-corredera',
    placeIds: ['a-sorolla', 'a-templo-debod', 'a-retiro', 'p-cafe-teresa', 'p-panaderia-sol', 'p-taberna-nueve', 'a-mercado-cebada'],
  },
]

/* ---------------- lookups ---------------- */

export const hotelById = (id: string) => HOTELS.find((h) => h.id === id)
export const placeById = (id: string) => PLACES.find((p) => p.id === id)
