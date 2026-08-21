# Q1609: access token captured in the signing closure in types.ts

## Question
The signer closure captures the access token at construction; can an attacker keep a stale closure alive so PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') signs using a token belonging to a previous session?

## Target
- File/function: [src/wallet-api/types.ts](src/wallet-api/types.ts) - PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry'), 1800000ms window
- Entrypoint: every signed wallet-api envelope
- Attacker controls: the expiry value chosen by the client clock
- Exploit idea: Obtain the closure, change sessions, then sign.
- Invariant to test: Signing must resolve the current session token at call time.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: change sessions and assert PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') refuses to reuse the captured token.
