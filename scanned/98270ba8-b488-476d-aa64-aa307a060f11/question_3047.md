# Q3047: connections list fetched per request in throwIfNotLoggedIn.ts

## Question
getCrossAppConnections is fetched on each wallet action; can an attacker cause the list to change between the resolution and the request in throwIfNotLoggedIn(user): only checks the user object passed by the caller so the token is sent to a different provider than the one authorised?

## Target
- File/function: [src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts](src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts) - throwIfNotLoggedIn(user): only checks the user object passed by the caller
- Entrypoint: every crossApp.wallet action
- Attacker controls: the user object supplied by the caller rather than read from session
- Exploit idea: Change the connections response between the two awaits.
- Invariant to test: Provider identity must be pinned for the duration of an operation.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Integration test: swap the connections mid-call in throwIfNotLoggedIn(user): only checks the user object passed by the caller and assert abort.
