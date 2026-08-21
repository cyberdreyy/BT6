# Q0627: callback url supplied by the caller in throwIfNotLoggedIn.ts

## Question
The callbackUrl and redirectUrl come from the caller; can an attacker set them through every crossApp.wallet action so the cross-app result (and any credential in the redirect) is delivered to an origin they control?

## Target
- File/function: [src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts](src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts) - throwIfNotLoggedIn(user): only checks the user object passed by the caller
- Entrypoint: every crossApp.wallet action
- Attacker controls: the user object supplied by the caller rather than read from session
- Exploit idea: Call the action with an attacker-controlled redirectUrl.
- Invariant to test: Callback targets must be constrained to the app's configured origins.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: pass a foreign redirectUrl to throwIfNotLoggedIn(user): only checks the user object passed by the caller and assert rejection.
