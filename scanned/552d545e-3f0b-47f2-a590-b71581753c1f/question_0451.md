# Q0451: mode parameter escalates link into login in FarcasterV2Api.ts

## Question
Can an unprivileged attacker pass a mode value to FarcasterV2Api.initializeAuth that turns an account-linking action into a login-or-sign-up, so the credential they control becomes a new authenticated session rather than a link on the existing account?

## Target
- File/function: [src/client/auth/FarcasterV2Api.ts](src/client/auth/FarcasterV2Api.ts) - FarcasterV2Api.initializeAuth, authenticate
- Entrypoint: privy.auth.farcasterV2.authenticate({message, signature, fid})
- Attacker controls: SIWF message, signature, fid
- Exploit idea: Call privy.auth.farcasterV2.authenticate({message, signature, fid}) with the mode field flipped and inspect which route and which session-update path executes.
- Invariant to test: The mode argument must never let a caller convert a link request into a session-issuing login inside src/client/auth/FarcasterV2Api.ts.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call FarcasterV2Api.initializeAuth with each accepted mode and assert updateWithTokensResponse is only reached for genuine login modes.
