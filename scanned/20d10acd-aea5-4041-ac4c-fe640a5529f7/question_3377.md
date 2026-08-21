# Q3377: communicationMode fixed to redirect in throwIfNotLoggedIn.ts

## Question
The transact URL pins communicationMode=redirect; can an attacker exploit the redirect mode through throwIfNotLoggedIn(user): only checks the user object passed by the caller so credentials or results traverse the browser address bar where other parties observe them?

## Target
- File/function: [src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts](src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts) - throwIfNotLoggedIn(user): only checks the user object passed by the caller
- Entrypoint: every crossApp.wallet action
- Attacker controls: the user object supplied by the caller rather than read from session
- Exploit idea: Trace what appears in the address bar and referrer during the flow.
- Invariant to test: Sensitive cross-app material must not traverse navigable URLs.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert throwIfNotLoggedIn(user): only checks the user object passed by the caller carries the token out-of-band rather than in the navigation.
