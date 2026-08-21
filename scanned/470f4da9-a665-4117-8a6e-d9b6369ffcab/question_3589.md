# Q3589: wallet-api errors surface raw responses in types.ts

## Question
Errors from these routes are wrapped with code and error text; can an attacker trigger an error through PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') whose message discloses another user's wallet identifiers?

## Target
- File/function: [src/wallet-api/types.ts](src/wallet-api/types.ts) - PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry'), 1800000ms window
- Entrypoint: every signed wallet-api envelope
- Attacker controls: the expiry value chosen by the client clock
- Exploit idea: Force error responses and inspect the propagated text.
- Invariant to test: Error text must not disclose identifiers of resources the caller does not own.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: force a 403 in PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') and assert the surfaced error carries no foreign identifiers.
