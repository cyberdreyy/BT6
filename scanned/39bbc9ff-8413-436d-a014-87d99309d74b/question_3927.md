# Q3927: action factories bound to a client at import in throwIfNotLoggedIn.ts

## Question
The crossApp barrel binds actions to a client instance; can an attacker retain a bound action from one session and invoke it after a switch through throwIfNotLoggedIn(user): only checks the user object passed by the caller?

## Target
- File/function: [src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts](src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts) - throwIfNotLoggedIn(user): only checks the user object passed by the caller
- Entrypoint: every crossApp.wallet action
- Attacker controls: the user object supplied by the caller rather than read from session
- Exploit idea: Bind actions as user A, switch to B, then invoke.
- Invariant to test: Bound actions must revalidate the session on each call.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: invoke a stale bound action from throwIfNotLoggedIn(user): only checks the user object passed by the caller after a switch and assert refusal.
