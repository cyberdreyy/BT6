# Q3809: get-wallet is unsigned in types.ts

## Question
getWallet issues a plain authenticated GET with no authorization signature; can an attacker use PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') to enumerate wallet metadata (ids, signers) for wallets reachable with a session token alone?

## Target
- File/function: [src/wallet-api/types.ts](src/wallet-api/types.ts) - PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry'), 1800000ms window
- Entrypoint: every signed wallet-api envelope
- Attacker controls: the expiry value chosen by the client clock
- Exploit idea: Call the read path for wallet ids not owned by the session.
- Invariant to test: Wallet metadata reads must be scoped to the authenticated owner.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Integration test: read a foreign wallet id through PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') and assert refusal.
