// Shared money formatting — every price in this app is stored/transmitted in cents.
export function formatMoney(cents: number): string {
  return (cents / 100).toFixed(2)
}

// rowName and seatLabel are independent organizer-typed fields, so they
// need an explicit separator rather than bare concatenation.
export function formatSeatLabel(sectionName: string, rowName: string, seatLabel: string): string {
  return `${sectionName}, Row ${rowName}, Seat ${seatLabel}`
}
