# Q1389: app id is the only signed header in types.ts

## Question
The signed headers contain privy-app-id and expiry only; can an attacker exploit unsigned but security-relevant headers (client id, ca-id, native app identifier) in PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') to change server-side treatment of the request?

## Target
- File/function: [src/wallet-api/types.ts](src/wallet-api/types.ts) - PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry'), 1800000ms window
- Entrypoint: every signed wallet-api envelope
- Attacker controls: the expiry value chosen by the client clock
- Exploit idea: Vary the unsigned headers and observe server-side behaviour differences.
- Invariant to test: All authorization-relevant headers must be signed.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') signs every header it sends that affects authorization.
