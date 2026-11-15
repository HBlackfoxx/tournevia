import { t } from './i18n';

/**
 * Equipment the product's own data uses, with the words a dispatcher and a driver say. Records keep
 * the codes (files, the API and the CSV export use them); any other code stays valid and is shown as
 * written.
 */
export const equipment = [
  { code: 'TAIL_LIFT', label: 'Tail lift' },
  { code: 'REFRIGERATED', label: 'Refrigerated body' },
] as const;

const known = new Map<string, string>(equipment.map((e) => [e.code, e.label]));

export const isNamedEquipment = (code: string) => known.has(code);

/** "TAIL_LIFT" → "Tail lift" / "Hayon élévateur"; an unknown code as written. */
export function equipmentName(code: string): string {
  const label = known.get(code);
  return label ? t(label) : code;
}

export function equipmentList(codes: string[]): string {
  return codes.map(equipmentName).join(', ');
}
