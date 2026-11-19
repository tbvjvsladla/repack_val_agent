/**
 * Formats cents as a dollar string with two decimals.
 */
export function formatCents(cents: number): string {
  // @ts-ignore
  return (cents / 100).toFixed(2);
}
