# Q2929: signature not bound to the access token in types.ts

## Question
The envelope commits to app id and expiry but not to the session token used to authenticate; can an attacker present a signature from PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') together with a different session token?

## Target
- File/function: [src/wallet-api/types.ts](src/wallet-api/types.ts) - PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry'), 1800000ms window
- Entrypoint: every signed wallet-api envelope
- Attacker controls: the expiry value chosen by the client clock
- Exploit idea: Pair a captured signature with another token.
- Invariant to test: Authorization signatures must be bound to the session that produced them.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: cross a captured signature with another session and assert rejection.
