import { describe, expect, it } from 'vitest'
import { formatSeatLabel } from './format'

describe('formatSeatLabel', () => {
  it('separates section, row, and seat with no ambiguous concatenation', () => {
    expect(formatSeatLabel('General', '1', '1-1')).toBe('General, Row 1, Seat 1-1')
  })
})
