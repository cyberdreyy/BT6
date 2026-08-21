# Q0454: mode parameter escalates link into login in GuestApi.ts

## Question
Can an unprivileged attacker pass a mode value to GuestApi.create that turns an account-linking action into a login-or-sign-up, so the credential they control becomes a new authenticated session rather than a link on the existing account?

## Target
- File/function: [src/client/auth/GuestApi.ts](src/client/auth/GuestApi.ts) - GuestApi.create, session.getOrCreateGuestCredential (privy:guest:<appId>)
- Entrypoint: privy.auth.guest.create()
- Attacker controls: guest credential value persisted in localStorage, repeated create calls
- Exploit idea: Call privy.auth.guest.create() with the mode field flipped and inspect which route and which session-update path executes.
- Invariant to test: The mode argument must never let a caller convert a link request into a session-issuing login inside src/client/auth/GuestApi.ts.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call GuestApi.create with each accepted mode and assert updateWithTokensResponse is only reached for genuine login modes.
