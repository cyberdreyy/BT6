# Q2057: logged-in check uses the caller's user object in throwIfNotLoggedIn.ts

## Question
throwIfNotLoggedIn only inspects the user object handed in by the caller; can an attacker pass a fabricated user through every crossApp.wallet action so throwIfNotLoggedIn(user): only checks the user object passed by the caller proceeds without a real session?

## Target
- File/function: [src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts](src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts) - throwIfNotLoggedIn(user): only checks the user object passed by the caller
- Entrypoint: every crossApp.wallet action
- Attacker controls: the user object supplied by the caller rather than read from session
- Exploit idea: Call the wallet action with a hand-built user object and no session.
- Invariant to test: Authorization checks must consult the session, not caller-supplied data.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call throwIfNotLoggedIn(user): only checks the user object passed by the caller with a fabricated user and no tokens and assert refusal.
