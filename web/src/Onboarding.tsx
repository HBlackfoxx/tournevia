import { getLanguage, t, useLanguage } from './i18n';
import { useEffect, useState } from 'react';
import { Icon } from './Brand';
import { MapPanel } from './MapPanel';
import { api, clearSessionData, setSession, type AddressCandidate, type SessionRecord } from './api';
import { AddressField } from './addressLookup';

// Shown next to IGN's results, as its licence asks.
const IGN = 'IGN Géoplateforme · Base Adresse Nationale (Licence Ouverte 2.0)';
import { invalidMessage, usernameRule } from './formValues';

export function Onboarding({
  enabled,
  onComplete,
  mapStyle = '',
  addressLookup,
}: {
  enabled: boolean;
  onComplete: () => void;
  mapStyle?: string;
  /** The installation's own address lookup; without one, setup can choose IGN's service. */
  addressLookup?: string | null;
}) {
  useLanguage();
  const [showPassword, setShowPassword] = useState(false),
    [picking, setPicking] = useState(false);
  const [step, setStep] = useState(1);
  const [company, setCompany] = useState(''),
    [depot, setDepot] = useState(''),
    [address, setAddress] = useState(''),
    [publicLookup, setPublicLookup] = useState(false),
    [latitude, setLatitude] = useState(''),
    [longitude, setLongitude] = useState(''),
    [reviewed, setReviewed] = useState(false);
  const [name, setName] = useState(''),
    [username, setUsername] = useState(''),
    [password, setPassword] = useState(''),
    [confirmation, setConfirmation] = useState('');
  const [token, setToken] = useState(() => {
    const value = new URLSearchParams(window.location.hash.slice(1)).get('setup') ?? '';
    return value;
  });
  const [manualCode, setManualCode] = useState(!token);
  // Opened when Continue finds the coordinates missing while the section is closed.
  const [showCoordinates, setShowCoordinates] = useState(false);
  useEffect(() => {
    if (new URLSearchParams(window.location.hash.slice(1)).has('setup'))
      window.history.replaceState(null, '', window.location.pathname + window.location.search);
  }, []);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  if (!enabled)
    return (
      <section className="workspace">
        <h2>{t('Installation setup is locked')}</h2>
        <p>
          {t(
            'The installation operator must enable the private setup link before the first administrator can be created.',
          )}
        </p>
        <button onClick={onComplete}>{t('Check again')}</button>
      </section>
    );
  return (
    <section className={`onboarding setup-step-${step}`} aria-label={t('Set up your workspace')}>
      <ol className="setup-steps" aria-label={t('Setup progress')}>
        {[t('Company & depot'), t('Administrator')].map((title, i) => (
          <li
            key={title}
            className={step > i + 1 ? 'complete' : ''}
            aria-current={step === i + 1 ? 'step' : undefined}
          >
            <span>{step > i + 1 ? <Icon name="check" /> : i + 1}</span>
            {title}
          </li>
        ))}
      </ol>
      <h1>{step === 1 ? t('Set up your company and depot') : t('Create the administrator account')}</h1>
      <p className="setup-intro">
        {step === 1
          ? t('Choose the vehicle entrance, not the centre of the building.')
          : t(
              'This administrator can manage users and depot settings. A short guide then helps you add vehicles, delivery numbers and first deliveries, or try with example deliveries.',
            )}
      </p>
      <form
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          setError('');
          // The browser's own check stops silently at a field inside a closed section and speaks the
          // browser's language: check here, open the section, name the field and move to it.
          const invalid = Array.from(e.currentTarget.elements).find(
            (element): element is HTMLInputElement =>
              element instanceof HTMLInputElement && !element.validity.valid,
          );
          if (invalid) {
            const section = invalid.closest('details');
            if (section?.classList.contains('coordinates')) setShowCoordinates(true);
            else if (section) setManualCode(true);
            const label = invalid.closest('label');
            const field =
              invalid.getAttribute('aria-label') ??
              (label?.firstChild?.nodeType === Node.TEXT_NODE
                ? label.firstChild.textContent
                : label?.textContent);
            setError(
              invalid.type === 'checkbox'
                ? t('Confirm that you reviewed the vehicle entrance on the map.')
                : section?.classList.contains('coordinates') && invalid.validity.valueMissing
                  ? t('Pick an address suggestion or place the pin on the map to set the depot coordinates.')
                  : `${field?.trim() ?? ''}: ${invalidMessage(invalid)}`,
            );
            window.setTimeout(() => invalid.focus());
            return;
          }
          if (step < 2) {
            setStep(step + 1);
            return;
          }
          if (password !== confirmation) {
            setError(t('Passwords do not match.'));
            return;
          }
          if (usernameRule(username)) {
            setError(t(usernameRule(username)));
            return;
          }
          setBusy(true);
          void api<SessionRecord>('setup', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              setup_token: token,
              company_name: company,
              display_name: name,
              username,
              password,
              public_address_lookup: !addressLookup && publicLookup,
              language: getLanguage(),
              depot: {
                code: 'MAIN',
                name: depot,
                address,
                timezone: 'Europe/Paris',
                location: { latitude: Number(latitude), longitude: Number(longitude), reviewed },
              },
            }),
          })
            .then((record) => {
              clearSessionData();
              setSession(record);
              setPassword('');
              setConfirmation('');
              setToken('');
              onComplete();
            })
            .catch((e) => {
              setPassword('');
              setConfirmation('');
              setError(e instanceof Error ? e.message : t('Setup could not be completed.'));
            })
            .finally(() => setBusy(false));
        }}
      >
        {step === 1 && (
          <div className="setup-card setup-depot-grid">
            <div>
              <label>
                {t('Company name')}
                <input
                  autoComplete="organization"
                  required
                  maxLength={120}
                  value={company}
                  onChange={(e) => setCompany(e.target.value)}
                />
              </label>
              <small>{t('This identifies your company; it does not rename Tournevia.')}</small>
              <>
                <label>
                  {t('Depot name')}
                  <input required maxLength={120} value={depot} onChange={(e) => setDepot(e.target.value)} />
                </label>
                {!addressLookup && (
                  <label className="check setup-public-lookup">
                    <input
                      type="checkbox"
                      checked={publicLookup}
                      onChange={(e) => setPublicLookup(e.target.checked)}
                    />
                    <span>
                      {t('Find addresses with the IGN address service (France)')}
                      <small>
                        {t(
                          'Free and without an account. Typed addresses are sent to the IGN Géoplateforme (data.geopf.fr). You can change this later in Settings › Address search.',
                        )}
                      </small>
                    </span>
                  </label>
                )}
                <AddressField
                  label="Depot address"
                  name="depot-address"
                  value={address}
                  attribution={addressLookup ?? (publicLookup ? IGN : null)}
                  maxLength={500}
                  search={(query) =>
                    api<AddressCandidate[]>(
                      `setup/geocode?q=${encodeURIComponent(query)}&public=${!addressLookup && publicLookup}`,
                      { headers: { 'X-Setup-Token': token } },
                    )
                  }
                  onChange={setAddress}
                  onPick={(c) => {
                    setAddress(c.label);
                    setLatitude(String(c.latitude));
                    setLongitude(String(c.longitude));
                    setReviewed(false);
                    setPicking(false);
                  }}
                />
                <details className="coordinates" open={showCoordinates || (!addressLookup && !publicLookup)}>
                  <summary>{t('Enter coordinates')}</summary>
                  <div className="fields">
                    <label>
                      {t('Depot latitude')}
                      <input
                        type="number"
                        required
                        step="any"
                        min={-90}
                        max={90}
                        value={latitude}
                        onChange={(e) => {
                          setLatitude(e.target.value);
                          setReviewed(false);
                        }}
                      />
                    </label>
                    <label>
                      {t('Depot longitude')}
                      <input
                        type="number"
                        required
                        step="any"
                        min={-180}
                        max={180}
                        value={longitude}
                        onChange={(e) => {
                          setLongitude(e.target.value);
                          setReviewed(false);
                        }}
                      />
                    </label>
                  </div>
                </details>
                <div className="info">
                  <strong>Europe/Paris</strong>
                  <p>{t('Planning dates and service windows use this timezone.')}</p>
                </div>
                <label className="check">
                  <input
                    type="checkbox"
                    required
                    checked={reviewed}
                    onChange={(e) => setReviewed(e.target.checked)}
                  />
                  {t('I reviewed this vehicle entrance on the map')}
                </label>
              </>
            </div>
            <div className="setup-map">
              <div className="map-action-wrap">
                {mapStyle && (
                  <MapPanel
                    style={mapStyle}
                    points={[]}
                    draft={
                      latitude !== '' && longitude !== ''
                        ? { latitude: Number(latitude), longitude: Number(longitude), reviewed }
                        : null
                    }
                    picking={picking}
                    onPick={(point) => {
                      setLatitude(String(point.latitude));
                      setLongitude(String(point.longitude));
                      setReviewed(false);
                      setPicking(false);
                    }}
                    onSelect={() => {}}
                  />
                )}
                <button type="button" className="secondary map-action" onClick={() => setPicking((v) => !v)}>
                  {picking ? t('Stop placing pin') : t('Place depot entrance on map')}
                </button>
              </div>
              <small>
                {t(
                  'Find the address, then move the pin to the vehicle entrance. Moving the pin resets its review.',
                )}
              </small>
            </div>
          </div>
        )}
        {step === 2 && (
          <div className="setup-card">
            <div className="fields">
              <label>
                {t('Your name')}
                <input
                  required
                  autoComplete="name"
                  maxLength={120}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </label>
              <label>
                {t('Username')}
                <input
                  required
                  autoComplete="username"
                  maxLength={80}
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                />
              </label>
              <label>
                {t('Password')}
                <span className="password-field">
                  <input
                    type={showPassword ? 'text' : 'password'}
                    required
                    minLength={15}
                    maxLength={128}
                    autoComplete="new-password"
                    aria-label={t('Password')}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                  />
                  <button
                    type="button"
                    className="text-button"
                    aria-label={showPassword ? t('Hide passwords') : t('Show passwords')}
                    onClick={() => setShowPassword((v) => !v)}
                  >
                    <Icon name="eye" />
                  </button>
                </span>
                <small>{t('At least 15 characters. Password-manager autofill is supported.')}</small>
              </label>
              <label>
                {t('Confirm password')}
                <input
                  type={showPassword ? 'text' : 'password'}
                  required
                  minLength={15}
                  maxLength={128}
                  autoComplete="new-password"
                  value={confirmation}
                  onChange={(e) => setConfirmation(e.target.value)}
                />
              </label>
            </div>
            <div className="setup-authorization">
              {token && (
                <div className="info">
                  <strong>{t('Private setup link or code supplied')}</strong>
                  <p>{t('Authorization will be verified when you create the workspace.')}</p>
                </div>
              )}
              <details open={manualCode} onToggle={(e) => setManualCode(e.currentTarget.open)}>
                <summary>{t('Use a setup code instead')}</summary>
                <label>
                  {t('Private setup code')}
                  <input
                    type="password"
                    required
                    minLength={32}
                    maxLength={256}
                    autoComplete="off"
                    value={token}
                    onChange={(e) => setToken(e.target.value)}
                  />
                </label>
              </details>
              <small>
                {t(
                  'Your installation operator supplies the private code; it is never shown in a confirmation summary.',
                )}
              </small>
            </div>
          </div>
        )}
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        <div className="setup-actions">
          {step > 1 ? (
            <button
              type="button"
              className="secondary"
              disabled={busy}
              onClick={() => {
                setError('');
                setStep(step - 1);
              }}
            >
              {t('Back')}
            </button>
          ) : (
            <small>{t('Your company name will be separate from the product brand.')}</small>
          )}
          <button disabled={busy}>
            {busy ? t('Creating workspace…') : step < 2 ? t('Continue') : t('Create workspace')}
          </button>
        </div>
      </form>
    </section>
  );
}
