import { t, useLanguage } from './i18n';
/** The Tournevia mark: a route through two stops into its destination, in the text colour so it
 * follows the theme. Decorative beside the name. */
export function Mark({ size = 36 }: { size?: number }) {
  return (
    <svg className="brand-mark" width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M5.75 18.71C6.65 18.28 7.85 17.72 8.75 17.29M12.55 15.51C14.9 14.4 17.95 13.9 17.95 12.2"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
      />
      <circle cx="3.85" cy="19.6" r="2.1" fill="currentColor" />
      <circle cx="10.65" cy="16.4" r="2.1" fill="currentColor" />
      <path
        fill="currentColor"
        fillRule="evenodd"
        d="M17.95 13.7L14.53 9.2A4.3 4.3 0 1 1 21.37 9.2ZM17.95 5.05a1.55 1.55 0 1 0 0 3.1a1.55 1.55 0 1 0 0 -3.1Z"
      />
    </svg>
  );
}

export function Brand() {
  useLanguage();
  return (
    <a className="wordmark" href="/" aria-label={t('Tournevia home')}>
      <Mark />
      <span>Tournevia</span>
    </a>
  );
}

export function NavIcon({ name }: { name: string }) {
  useLanguage();
  const paths: Record<string, string> = {
    users:
      'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8M20 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8',
    company: 'M4 21V3h10v18M2 21h20M14 10h6v11M7 7h4M7 11h4M7 15h4M7 19h4',
    planning: 'M5 4v9a4 4 0 0 0 4 4h8M9 4h6v7M3 2h4v4H3zM17 15h4v4h-4z',
    orders: 'm3 7 9-5 9 5v10l-9 5-9-5V7zm0 0 9 5 9-5M12 12v10M7 4l10 6',
    customers: 'M3 21h18M5 21V8l7-5 7 5v13M9 21v-6h6v6M9 11h.01M15 11h.01',
    vehicles:
      'M2 5h12v12H2zM14 10h4l4 5v2h-8M5 17a2 2 0 1 0 0 4 2 2 0 0 0 0-4M18 17a2 2 0 1 0 0 4 2 2 0 0 0 0-4',
    saved: 'M5 3h14v19l-7-5-7 5V3z',
    addresses: 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3',
    person: 'M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8',
    examples: 'M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.7 3h10.6a2 2 0 0 0 1.7-3l-5-9V3M7.5 15h9',
    numbers: 'M9 4 7 20M17 4l-2 16M4 9h16M3 15h16',
    calculation: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 7v5l3.5 2',
    settings: 'M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1 1-3zM8 12a4 4 0 1 0 8 0 4 4 0 1 0-8 0',
  };
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinejoin="round"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <path d={paths[name]} />
    </svg>
  );
}

const icons = {
  check: 'M5 12.5 10 17l9-10',
  alert: 'M12 3.5 2.5 20h19L12 3.5zM12 10v4.5M12 17.2v.1',
  info: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 11v5.5M12 7.8v.1',
  clock: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 7v5l3.5 2',
  route:
    'M6 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4M18 9a2 2 0 1 0 0-4 2 2 0 0 0 0 4M8 17h7a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h7',
  menu: 'M4 7h16M4 12h16M4 17h16',
  close: 'M6 6l12 12M18 6 6 18',
  print: 'M7 9V3.5h10V9M7 17.5H4.5V9h15v8.5H17M7 14h10v6.5H7z',
  map: 'M9 4 3 6.5v13.5l6-2.5 6 2.5 6-2.5V4l-6 2.5L9 4zM9 4v13.5M15 6.5V20',
  list: 'M9 6h11M9 12h11M9 18h11M4.5 6v.1M4.5 12v.1M4.5 18v.1',
  chevron: 'm6 9 6 6 6-6',
  refresh: 'M20 12a8 8 0 1 1-2.3-5.7M20 4.5v4.5h-4.5',
  shield: 'M12 3 4.5 6v6c0 4.4 3.2 7.9 7.5 9 4.3-1.1 7.5-4.6 7.5-9V6L12 3z',
  eye: 'M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z',
  signout: 'M14 4h5v16h-5M10 8l-4 4 4 4M6 12h10',
  sun: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4',
  moon: 'M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z',
  // Map markers: a depot is a warehouse, another place (a driver's home, a car park) a flag.
  depot: 'M3 10 12 4.5 21 10v10H3zM7.5 20v-6h9v6M7.5 17h9',
  place: 'M6 21V3.5M6 4.5h11l-2.5 4 2.5 4H6',
};
export type IconName = keyof typeof icons;

/** The same outline icon as a DOM node, for markers that maps draw outside React. */
export function iconElement(name: IconName, size = 16): SVGSVGElement {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  for (const [key, value] of Object.entries({
    class: 'icon',
    width: size,
    height: size,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    'stroke-width': 2,
    'stroke-linejoin': 'round',
    'stroke-linecap': 'round',
    'aria-hidden': 'true',
  }))
    svg.setAttribute(key, String(value));
  const path = document.createElementNS(ns, 'path');
  path.setAttribute('d', icons[name]);
  svg.append(path);
  return svg;
}

/** One outline icon set for states and affordances; decorative, so screen readers skip it. */
export function Icon({ name, size = 16 }: { name: IconName; size?: number }) {
  return (
    <svg
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinejoin="round"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <path d={icons[name]} />
    </svg>
  );
}
