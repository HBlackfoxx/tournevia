import { useSyncExternalStore } from 'react';
import fr from './locales/fr.json';
import { readSetting, writeSetting } from './settings';

export type Language = 'en' | 'fr';
const listeners = new Set<() => void>();
function initialLanguage(): Language {
  const saved = readSetting('language');
  if (saved === 'en' || saved === 'fr') return saved;
  return typeof navigator !== 'undefined' && navigator.language.startsWith('fr') ? 'fr' : 'en';
}
let language = initialLanguage();
if (typeof document !== 'undefined') document.documentElement.lang = language;
export function getLanguage() {
  return language;
}
/** Date formatting locale for the active language; English keeps day-first European dates. */
export function locale() {
  return language === 'fr' ? 'fr-FR' : 'en-GB';
}
export function setLanguage(value: Language) {
  language = value;
  document.documentElement.lang = value;
  // The account remains authoritative when this browser keeps nothing.
  writeSetting('language', value);
  listeners.forEach((listener) => listener());
}
export function useLanguage() {
  return useSyncExternalStore((callback) => {
    listeners.add(callback);
    return () => listeners.delete(callback);
  }, getLanguage);
}
/** A count with its noun in the right grammatical number for the interface language (French: 0 and 1 are singular). */
export function plural(count: number, one: string, other: string): string {
  return t(new Intl.PluralRules(locale()).select(count) === 'one' ? one : other, count);
}
/** Translate UI copy only. Identifiers, user data and API enum values stay unchanged. */
// Server messages that end with details ("Expected exact headers: …") translate by their fixed start.
const prefixes = Object.keys(fr)
  .filter((key) => /[:.] $/.test(key))
  .sort((a, b) => b.length - a.length);
function french(source: string): string {
  const exact = (fr as Record<string, string>)[source];
  if (exact !== undefined) return exact;
  const prefix = prefixes.find((key) => source.startsWith(key));
  if (prefix) return (fr as Record<string, string>)[prefix] + source.slice(prefix.length);
  // Engine messages join a sentence naming records with fixed advice: translate what is fixed.
  const sentences = source.split(/(?<=[.!?])\s+(?=[A-Z])/);
  return sentences.length > 1
    ? sentences.map((s) => (fr as Record<string, string>)[s] ?? s).join(' ')
    : source;
}
/** French typography: the space before : ; ? ! and inside « » never breaks, so a line never starts
 * with the punctuation. Applied to the translated copy only, before values are filled in. */
const frenchSpaces = (text: string) => text.replace(/ ([:;?!»])/g, ' $1').replace(/« /g, '« ');
export function t(source: string | null | undefined, ...values: unknown[]): string {
  if (source == null) return '';
  const text = language === 'fr' ? frenchSpaces(french(source)) : source;
  return text.replace(/\{(\d+)\}/g, (_, index) => String(values[Number(index)] ?? ''));
}
