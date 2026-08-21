# Q2277: read_only flag is the only authorization gate in throwIfNotLoggedIn.ts

## Question
sendCrossAppRequest rejects only when the connection is marked read_only; can an attacker influence the connections response so throwIfNotLoggedIn(user): only checks the user object passed by the caller treats a read-only connection as transactable?

## Target
- File/function: [src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts](src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts) - throwIfNotLoggedIn(user): only checks the user object passed by the caller
- Entrypoint: every crossApp.wallet action
- Attacker controls: the user object supplied by the caller rather than read from session
- Exploit idea: Return a connection without the read_only flag.
- Invariant to test: Transaction authority must be established server-side, not by a client-visible flag.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: omit read_only in throwIfNotLoggedIn(user): only checks the user object passed by the caller's stub and assert the SDK still requires explicit authority.
