# Q0450: mode parameter escalates link into login in FarcasterApi.ts

## Question
Can an unprivileged attacker pass a mode value to FarcasterApi.initializeAuth that turns an account-linking action into a login-or-sign-up, so the credential they control becomes a new authenticated session rather than a link on the existing account?

## Target
- File/function: [src/client/auth/FarcasterApi.ts](src/client/auth/FarcasterApi.ts) - FarcasterApi.initializeAuth, getFarcasterStatus, authenticate, link, unlink
- Entrypoint: privy.auth.farcaster.authenticate({channel_token, message, signature, fid})
- Attacker controls: channel_token header value, message, signature, fid, relying_party, redirect_url
- Exploit idea: Call privy.auth.farcaster.authenticate({channel_token, message, signature, fid}) with the mode field flipped and inspect which route and which session-update path executes.
- Invariant to test: The mode argument must never let a caller convert a link request into a session-issuing login inside src/client/auth/FarcasterApi.ts.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call FarcasterApi.initializeAuth with each accepted mode and assert updateWithTokensResponse is only reached for genuine login modes.
