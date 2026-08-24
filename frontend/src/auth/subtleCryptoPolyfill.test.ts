import { describe, expect, it } from 'vitest'
import { sha256 } from './subtleCryptoPolyfill'

function toHex(bytes: Uint8Array): string {
  return Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

describe('sha256', () => {
  it('matches the NIST test vector for the empty string', () => {
    expect(toHex(sha256(new TextEncoder().encode('')))).toBe(
      'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
    )
  })

  it('matches the NIST test vector for "abc"', () => {
    expect(toHex(sha256(new TextEncoder().encode('abc')))).toBe(
      'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
    )
  })

  it('matches for a 64-byte input crossing two 512-bit blocks after padding', () => {
    const input = 'a'.repeat(64)
    expect(toHex(sha256(new TextEncoder().encode(input)))).toBe(
      'ffe054fe7ae0cb6dc65c3af9b61d5209f439851db43d0ba5997337df154668eb',
    )
  })

  it('matches for a PKCE-shaped 43-character code_verifier', () => {
    const verifier = 'dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk'
    expect(toHex(sha256(new TextEncoder().encode(verifier)))).toBe(
      '13d31e961a1ad8ec2f16b10c4c982e0876a878ad6df144566ee1894acb70f9c3',
    )
  })
})
