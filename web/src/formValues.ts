import fieldLimits from './field-limits.json';
import { formatNumber } from './format';
import { t } from './i18n';

/** Bounds of one API field (generated from the backend models by scripts/export_contracts.py). */
export type Limit = {
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  pattern?: string;
  maxItems?: number;
};
export const limits = fieldLimits as Record<
  'depot' | 'vehicles' | 'orders' | 'customers' | 'users',
  Record<string, Limit>
>;
const username = new RegExp(limits.users.username.pattern!);
/** Why a username would be refused, or '' when it is acceptable. */
export const usernameRule = (value: string) =>
  username.test(value)
    ? ''
    : 'Usernames start with a letter or digit and use only letters, digits and . _ @ + -';
const code = new RegExp(limits.vehicles.skills.pattern!);
/** Equipment codes as typed, in the API's form: capitals, with spaces and hyphens as underscores. */
export const equipmentCodes = (typed: string) =>
  typed
    ? typed
        .toUpperCase()
        .replace(/[\s-]+/g, '_')
        .split('|')
    : [];
/** The first equipment code the API would refuse, if any. */
export const badEquipmentCode = (codes: string[]) => codes.find((c) => !code.test(c));

// Keep user-facing units separate from the integer API contract.
export function scaledValue(value: number, scale: number) {
  return Math.round(value * scale * 1e6) / 1e6;
}
export function parisCandidates(local: string): string[] {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(local)) return [];
  const normalized = local.length === 16 ? local + ':00' : local;
  return ['+01:00', '+02:00']
    .map((offset) => normalized + offset)
    .filter((iso) => {
      const date = new Date(iso);
      if (!Number.isFinite(date.getTime())) return false;
      const parts = new Intl.DateTimeFormat('sv-SE', {
        timeZone: 'Europe/Paris',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hourCycle: 'h23',
      })
        .format(date)
        .replace(' ', 'T');
      return parts === normalized;
    });
}

/** The interface's own wording for a field the browser finds invalid, instead of the browser's bubble
 * (which is in the browser's language and cannot reach a field inside a closed section). */
export function invalidMessage(input: HTMLInputElement): string {
  const v = input.validity,
    bound = (value: string) => formatNumber(Number(value), value.split('.')[1]?.length ?? 0);
  if (v.valueMissing) return t('This value is required.');
  if (v.badInput) return t('Enter a number.');
  if (v.rangeUnderflow) return t('Use a value of at least {0}.', bound(input.min));
  if (v.rangeOverflow) return t('Use a value of at most {0}.', bound(input.max));
  if (v.stepMismatch) return input.step === '1' ? t('Enter a whole number.') : t('Use fewer decimal places.');
  if (v.tooShort) return t('Use at least {0} characters.', input.minLength);
  if (v.tooLong) return t('Use at most {0} characters.', input.maxLength);
  return t('Check this value.');
}
