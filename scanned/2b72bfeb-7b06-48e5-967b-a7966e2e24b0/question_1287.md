# Q1287: listener not unsubscribed on failure in throwIfNotLoggedIn.ts

## Question
The unsubscribe in linkWithCrossAppAuth runs only after a successful link; can an attacker make the link throw so the listener stays attached and keeps capturing later tokens through throwIfNotLoggedIn(user): only checks the user object passed by the caller?

## Target
- File/function: [src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts](src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts) - throwIfNotLoggedIn(user): only checks the user object passed by the caller
- Entrypoint: every crossApp.wallet action
- Attacker controls: the user object supplied by the caller rather than read from session
- Exploit idea: Force the link to reject and then trigger another OAuth flow.
- Invariant to test: Listeners must be removed on every exit path.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: force a rejection in throwIfNotLoggedIn(user): only checks the user object passed by the caller and assert the listener is removed.
