// Preset city list for the live viewers (display layer ONLY — measurement
// fixtures come from the batch manifest, never from here). Client-safe data,
// searched client-side; no geocoding API dependency (static export stays
// self-contained). Coordinates are 4-decimal WGS84.

export interface City {
  name: string;
  lat: string;
  lon: string;
}

export const CITIES: City[] = [
  { name: "Abu Dhabi", lat: "24.4539", lon: "54.3773" },
  { name: "Amsterdam", lat: "52.3676", lon: "4.9041" },
  { name: "Athens", lat: "37.9838", lon: "23.7275" },
  { name: "Auckland", lat: "-36.8509", lon: "174.7645" },
  { name: "Bangkok", lat: "13.7563", lon: "100.5018" },
  { name: "Beijing", lat: "39.9042", lon: "116.4074" },
  { name: "Berlin", lat: "52.5200", lon: "13.4050" },
  { name: "Bogotá", lat: "4.7110", lon: "-74.0721" },
  { name: "Buenos Aires", lat: "-34.6037", lon: "-58.3816" },
  { name: "Cairo", lat: "30.0444", lon: "31.2357" },
  { name: "Cape Town", lat: "-33.9249", lon: "18.4241" },
  { name: "Changsha", lat: "28.2282", lon: "112.9388" },
  { name: "Chengdu", lat: "30.5728", lon: "104.0668" },
  { name: "Chicago", lat: "41.8781", lon: "-87.6298" },
  { name: "Chongqing", lat: "29.5630", lon: "106.5516" },
  { name: "Copenhagen", lat: "55.6761", lon: "12.5683" },
  { name: "Dalian", lat: "38.9140", lon: "121.6147" },
  { name: "Delhi", lat: "28.7041", lon: "77.1025" },
  { name: "Dubai", lat: "25.2048", lon: "55.2708" },
  { name: "Dublin", lat: "53.3498", lon: "-6.2603" },
  { name: "Guangzhou", lat: "23.1291", lon: "113.2644" },
  { name: "Hangzhou", lat: "30.2741", lon: "120.1551" },
  { name: "Harbin", lat: "45.8038", lon: "126.5350" },
  { name: "Helsinki", lat: "60.1699", lon: "24.9384" },
  { name: "Hong Kong", lat: "22.3193", lon: "114.1694" },
  { name: "Honolulu", lat: "21.3069", lon: "-157.8583" },
  { name: "Istanbul", lat: "41.0082", lon: "28.9784" },
  { name: "Jakarta", lat: "-6.2088", lon: "106.8456" },
  { name: "Johannesburg", lat: "-26.2041", lon: "28.0473" },
  { name: "Kuala Lumpur", lat: "3.1390", lon: "101.6869" },
  { name: "Kunming", lat: "24.8801", lon: "102.8329" },
  { name: "Lagos", lat: "6.5244", lon: "3.3792" },
  { name: "Lhasa", lat: "29.6520", lon: "91.1721" },
  { name: "Lima", lat: "-12.0464", lon: "-77.0428" },
  { name: "Lisbon", lat: "38.7223", lon: "-9.1393" },
  { name: "London", lat: "51.5074", lon: "-0.1278" },
  { name: "Los Angeles", lat: "34.0522", lon: "-118.2437" },
  { name: "Madrid", lat: "40.4168", lon: "-3.7038" },
  { name: "Melbourne", lat: "-37.8136", lon: "144.9631" },
  { name: "Mexico City", lat: "19.4326", lon: "-99.1332" },
  { name: "Moscow", lat: "55.7558", lon: "37.6173" },
  { name: "Mumbai", lat: "19.0760", lon: "72.8777" },
  { name: "Nairobi", lat: "-1.2921", lon: "36.8219" },
  { name: "Nanjing", lat: "32.0603", lon: "118.7969" },
  { name: "New York", lat: "40.7128", lon: "-74.0060" },
  { name: "Osaka", lat: "34.6937", lon: "135.5023" },
  { name: "Oslo", lat: "59.9139", lon: "10.7522" },
  { name: "Paris", lat: "48.8566", lon: "2.3522" },
  { name: "Prague", lat: "50.0755", lon: "14.4378" },
  { name: "Qingdao", lat: "36.0671", lon: "120.3826" },
  { name: "Reykjavík", lat: "64.1466", lon: "-21.9426" },
  { name: "Rio de Janeiro", lat: "-22.9068", lon: "-43.1729" },
  { name: "Rome", lat: "41.9028", lon: "12.4964" },
  { name: "San Francisco", lat: "37.7749", lon: "-122.4194" },
  { name: "São Paulo", lat: "-23.5505", lon: "-46.6333" },
  { name: "Seattle", lat: "47.6062", lon: "-122.3321" },
  { name: "Seoul", lat: "37.5665", lon: "126.9780" },
  { name: "Shanghai", lat: "31.2304", lon: "121.4737" },
  { name: "Shenzhen", lat: "22.5431", lon: "114.0579" },
  { name: "Singapore", lat: "1.3521", lon: "103.8198" },
  { name: "Stockholm", lat: "59.3293", lon: "18.0686" },
  { name: "Sydney", lat: "-33.8688", lon: "151.2093" },
  { name: "Taipei", lat: "25.0330", lon: "121.5654" },
  { name: "Tokyo", lat: "35.6762", lon: "139.6503" },
  { name: "Toronto", lat: "43.6532", lon: "-79.3832" },
  { name: "Ürümqi", lat: "43.8256", lon: "87.6168" },
  { name: "Vancouver", lat: "49.2827", lon: "-123.1207" },
  { name: "Vienna", lat: "48.2082", lon: "16.3738" },
  { name: "Warsaw", lat: "52.2297", lon: "21.0122" },
  { name: "Wuhan", lat: "30.5928", lon: "114.3055" },
  { name: "Xi'an", lat: "34.3416", lon: "108.9398" },
  { name: "Zürich", lat: "47.3769", lon: "8.5417" },
];

/** case/diacritic-light substring search over the preset list. */
export function searchCities(q: string): City[] {
  const needle = q.trim().toLowerCase();
  if (!needle) return CITIES;
  return CITIES.filter((c) => c.name.toLowerCase().includes(needle));
}

export const DELAY_PRESETS = [
  { label: "no delay", ms: 0 },
  { label: "500 ms", ms: 500 },
  { label: "2 s", ms: 2000 },
  { label: "5 s", ms: 5000 },
] as const;
