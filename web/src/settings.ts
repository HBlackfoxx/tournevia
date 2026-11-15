/** Settings this browser keeps for itself (appearance and language). Storage is optional
 * throughout: without it, a setting applies until the page is closed. */
const PREFIX = 'tournevia-';

export function readSetting(name: string): string | null {
  try {
    return localStorage.getItem(PREFIX + name);
  } catch {
    return null;
  }
}

/** Saves a setting, or forgets it with null; returns whether the browser kept it. */
export function writeSetting(name: string, value: string | null): boolean {
  try {
    if (value === null) localStorage.removeItem(PREFIX + name);
    else localStorage.setItem(PREFIX + name, value);
    return true;
  } catch {
    return false;
  }
}
