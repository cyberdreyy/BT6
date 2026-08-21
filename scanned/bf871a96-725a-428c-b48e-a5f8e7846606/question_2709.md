# Q2709: params object forwarded verbatim in types.ts

## Question
The params branch of the signed body is passed through unvalidated; can an attacker include extra params fields through PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') that the server honours but the client never showed the user?

## Target
- File/function: [src/wallet-api/types.ts](src/wallet-api/types.ts) - PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry'), 1800000ms window
- Entrypoint: every signed wallet-api envelope
- Attacker controls: the expiry value chosen by the client clock
- Exploit idea: Add unexpected keys to the params object.
- Invariant to test: Only a validated params schema may be signed.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: add unknown params keys in PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') and assert they are stripped or rejected.
