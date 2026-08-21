# Q1169: version field is a constant in types.ts

## Question
Every envelope sets version: 1; can an attacker exploit the absence of a nonce or request id so two identical operations from PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') produce byte-identical signatures that are interchangeable?

## Target
- File/function: [src/wallet-api/types.ts](src/wallet-api/types.ts) - PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry'), 1800000ms window
- Entrypoint: every signed wallet-api envelope
- Attacker controls: the expiry value chosen by the client clock
- Exploit idea: Issue the same operation twice and compare signatures.
- Invariant to test: Envelopes must include a unique per-request nonce.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: issue the same PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') operation twice and assert the signatures differ.
