# 3. Frontend

[Developer guide](README.md) › Frontend

The dispatcher interface is a single-page React application in [web/](../../web/). Vite builds it to
`web/dist`, and the API serves those files itself: in production there is no separate web server for the
application and no cross-origin request.

Main libraries (exact versions in [web/package.json](../../web/package.json)): React 19, TypeScript 5,
MapLibre GL for the map, Vite for the build, Vitest with Testing Library and jsdom for tests, ESLint and
Prettier. There is deliberately no router library, no state-management library and no CSS framework.

## Structure

```text
web/
  index.html            entry page
  public/assets/        logo, mark and icons
  src/
    main.tsx            mounts <App>, loads the theme, the Geist font and the two stylesheets
    App.tsx             startup: health, setup, session, configuration; sign-in or workspace
    Workspace.tsx       the signed-in layout: navigation, areas, lists, editors, the map
    *.tsx               one file per screen or panel, each with its *.test.tsx
    api.ts              the fetch wrapper and the type aliases taken from api-types.ts
    i18n.ts             t(), plural(), the active language
    locales/fr.json     French translations, keyed by the English text
    style.css           colour tokens (light and dark), base elements, shared components
    screens.css         styles of individual screens
    api-types.ts        GENERATED from backend/openapi.json
    field-limits.json   GENERATED from the backend models
    csv-columns.json    GENERATED from the CSV importer
    locales/server-messages.json   GENERATED list of server messages
```

Never edit the four generated files by hand; see [Development workflow](development.md#regenerate-contracts).

## Screens and where they live

The application keeps its location in the URL fragment, so the browser's back button and a reload work
without a router:

- `#/<area>?date=YYYY-MM-DD&plan=<id>`: the workspace (`readRoute` and `routeHash` in `Workspace.tsx`).
- `#/sheets?plan=<id>&version=<n>`: the drivers' route sheets of a READY plan, shown alone for printing
  (`readSheetRoute` in `RouteSheets.tsx`).

| Area (fragment) | Shown as | Main components |
|---|---|---|
| `planning` | Planning | [PlanningPanel.tsx](../../web/src/PlanningPanel.tsx) selects vehicles and deliveries, submits a calculation and polls the day's jobs; [RouteReview.tsx](../../web/src/RouteReview.tsx) shows a plan: routes on the map and in a list, drag-and-drop or keyboard moves, recalculation, Ready, CSV export, route sheets. [SystemStatus.tsx](../../web/src/SystemStatus.tsx) warns when the worker or routing service is down. |
| `orders` | Deliveries | `Workspace.tsx` lists and filters; [SourceEditor.tsx](../../web/src/SourceEditor.tsx) edits one record; [ImportPanel.tsx](../../web/src/ImportPanel.tsx) previews and commits a CSV file; [addressLookup.tsx](../../web/src/addressLookup.tsx) suggests addresses. |
| `customers` | Customers | [Customers.tsx](../../web/src/Customers.tsx) |
| `vehicles` | Fleet | `Workspace.tsx` and `SourceEditor.tsx`; [WorkingHours.tsx](../../web/src/WorkingHours.tsx) and [availability.tsx](../../web/src/availability.tsx) for weekly hours and days off. |
| `saved` | Saved plans | [SavedPlans.tsx](../../web/src/SavedPlans.tsx) |
| `settings` | Settings | [AccountPanel.tsx](../../web/src/AccountPanel.tsx) (account and users), [LanguageSettings.tsx](../../web/src/LanguageSettings.tsx) (language, password, theme), [Places.tsx](../../web/src/Places.tsx) (depots and places), [Numbering.tsx](../../web/src/Numbering.tsx), [CalculationSettings.tsx](../../web/src/CalculationSettings.tsx), [ExampleData.tsx](../../web/src/ExampleData.tsx), address lookup. |

Outside the workspace: [Onboarding.tsx](../../web/src/Onboarding.tsx) is first-use setup (company, depot,
first administrator), [SetupWizard.tsx](../../web/src/SetupWizard.tsx) is the guide shown right after it,
and `Login` in `AccountPanel.tsx` is the sign-in page.

Shared pieces: [MapPanel.tsx](../../web/src/MapPanel.tsx) wraps MapLibre (pins, routes, pin picking,
stale display); [Brand.tsx](../../web/src/Brand.tsx) holds the logo and inline SVG icons;
[confirm.tsx](../../web/src/confirm.tsx) is the application's own yes/no dialog (use `ask()`, never
`window.confirm`); [PageActions.tsx](../../web/src/PageActions.tsx) places buttons in the page header;
[format.ts](../../web/src/format.ts) formats numbers, euros, distances, weights and dates in the interface
language; [equipment.ts](../../web/src/equipment.ts) names equipment codes; [formValues.ts](../../web/src/formValues.ts)
holds form rules and limits.

## Talking to the API

All requests go through `api<T>(path, options)` in [api.ts](../../web/src/api.ts). It:

- prefixes `/api/v1/` and sends cookies (`credentials: 'same-origin'`);
- adds `X-VRP-Request: 1` and, once signed in, `X-CSRF-Token`, which the server requires on writes;
- throws `ApiError` with the HTTP status, the server's `code`, the translated message, per-field messages
  for validation errors (`fields`), and the `identifiers` the message is about;
- on a 401, dispatches `vrp-session-expired`: `App.tsx` then shows a sign-in dialog **over** the open
  workspace, so unsaved route moves and selections survive a session expiry.

`save(path, data, version?)` posts a new record, or puts an existing one with `If-Match: "<version>"`.
Reads that return an `ETag` give the version to send back.

Types come from the backend: `api.ts` exports aliases such as
`type Vehicle = components['schemas']['Owned_VehicleSource_']` from the generated `api-types.ts`. When a
backend model changes, regenerate the types and let TypeScript show every place to update.

Patterns used for long operations:

- **Calculations and edits** are submitted with an `Idempotency-Key` from `crypto.randomUUID()`. The key
  and body are kept in `sessionStorage` (`vrp-pending:<day>`, `vrp-edit:<plan>`) until the server answers,
  so a retry after a network error or a reload reuses the same key and never creates a second job.
- **Polling** uses `whenVisible(run, delay)`, which pauses while the tab is hidden. The job list polls
  every 2 s while a calculation is active and every 20 s otherwise; the system status every 30 s. Polls send
  `X-VRP-Background: 1` (`background` in `api.ts`) so they do not keep an idle session alive. Real
  keyboard and pointer activity refreshes the session at most every 5 minutes.

## Translations

The interface is in English and French. The English text **is** the key:

```tsx
<h2>{t('Calculation')}</h2>
<p>{t('New calculations keep {0} min before each window ends.', chosen)}</p>
{plural(count, '{0} delivery', '{0} deliveries')}
```

- Add every new English string to [locales/fr.json](../../web/src/locales/fr.json) with its French text.
  A test reads every `t('…')` literal in the source files and fails if one is missing, and another checks
  that `{0}` placeholders match.
- `t()` only translates literals in the code and fixed server messages. Never translate identifiers, user
  data or API enum values.
- Server messages are fixed English sentences too. Their list is generated into
  `locales/server-messages.json`, and a test requires a French translation for each. Messages that end
  with details (`Independent re-validation failed: …`) are translated by their fixed beginning.
- French typography is applied automatically: the space before `:`, `;`, `?`, `!` becomes non-breaking.
- The language comes from the signed-in account (`identity.language`). Before sign-in, the browser's
  saved choice or its language is used. Dates use `fr-FR` or `en-GB` (day first in both).

Adding a third language would mean a second locale file, a `Language` value, and the server's
`ck_users_language` constraint and `Preferences` model; it is not a drop-in change today.

## Styles and themes

- [style.css](../../web/src/style.css) starts with the colour tokens (`--page`, `--surface`, `--text`,
  `--accent`, `--danger`, …) for light, then dark twice: once under `prefers-color-scheme: dark` when the
  user has not chosen light, once under `[data-theme='dark']`. The two dark blocks must stay identical;
  `theme.test.ts` checks it. Use the tokens; do not write colours in components.
- [screens.css](../../web/src/screens.css) holds per-screen rules. Class names are plain and descriptive;
  there are no CSS modules.
- The chrome is deliberately colourless (ink on a stone ground, white sheets); colour is reserved for
  route lines and status. Route colours are the `routeColors` list in `RouteReview.tsx`, chosen to stay
  distinct for up to 25 vehicles and readable with white pin numbers.
- [theme.ts](../../web/src/theme.ts) applies `system`, `light` or `dark` and stores the choice in this
  browser only ([settings.ts](../../web/src/settings.ts), wrapped in try/catch because storage may be
  unavailable).
- Every screen must work at phone width. Narrow screens switch the route review between List and Map.

## The map

`MapPanel` receives the style URL from `GET /api/v1/configuration` (`map_style_url`, set by the
`MAP_STYLE_URL` environment variable; OpenFreeMap Liberty by default). The basemap is decoration: if it
fails to load, lists, forms and coordinate fields stay usable. Routes drawn on it are the actual road
geometry returned by Valhalla, never straight lines presented as roads. To use another basemap, change
`MAP_STYLE_URL`; no code change is needed if the style is a MapLibre style JSON served over HTTPS.

## Tests

Screens and most helpers have a `*.test.tsx` next to them. Tests render the component with Testing Library and stub
`fetch`:

```tsx
const fetch = vi.fn(async () => ({ ok: true, json: async () => ({ minutes: 20 }) }));
vi.stubGlobal('fetch', fetch);
render(<CalculationSettings minutes={15} onChanged={changed} />);
fireEvent.change(screen.getByLabelText('Safety margin before each window ends'), { target: { value: '20' } });
```

Query elements the way a person finds them: by label, role and visible text. [test-setup.ts](../../web/src/test-setup.ts)
resets the language to English, the theme, the URL and all stubs between tests.
[test-server.ts](../../web/src/test-server.ts) has helpers that turn fixture vehicles into saved records
and answer list requests, and several tests import the
shared synthetic fixture `backend/tests/fixtures/paris-ten.json`.

## Build output

`npm run build` type-checks (`tsc --noEmit`), builds with Vite, then runs
[scripts/frontend_notices.mjs](../../scripts/frontend_notices.mjs), which writes
`dist/assets/THIRD_PARTY_NOTICES.txt` and `dist/assets/dependency-inventory.json` from the production
dependencies. The page footer links to those notices. The API serves `index.html` with `no-cache` so a new
version replaces the old hashed bundles at once after an upgrade.
