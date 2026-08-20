// Shared money formatting — every price in this app is stored/transmitted in cents.
export function formatMoney(cents: number): string {
  return (cents / 100).toFixed(2)
}
