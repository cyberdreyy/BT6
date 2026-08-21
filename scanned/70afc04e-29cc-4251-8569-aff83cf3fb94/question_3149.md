# Q3149: json body serialised twice in types.ts

## Question
PrivyInternal.fetch JSON.stringifies the body while the signature covers the pre-serialisation object; can an attacker exploit serialisation differences (key order, unicode escaping, number formatting) so PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') signs one byte string and sends another?

## Target
- File/function: [src/wallet-api/types.ts](src/wallet-api/types.ts) - PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry'), 1800000ms window
- Entrypoint: every signed wallet-api envelope
- Attacker controls: the expiry value chosen by the client clock
- Exploit idea: Include unicode, large numbers and key orders that differ between canonicalize and JSON.stringify.
- Invariant to test: Signed and transmitted encodings must be byte-identical.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert canonicalize output and the transmitted body are byte-equal for PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry').
