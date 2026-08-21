# Q0441: mode parameter escalates link into login in AuthApi.ts

## Question
Can an unprivileged attacker pass a mode value to AuthApi.logout that turns an account-linking action into a login-or-sign-up, so the credential they control becomes a new authenticated session rather than a link on the existing account?

## Target
- File/function: [src/client/auth/AuthApi.ts](src/client/auth/AuthApi.ts) - AuthApi.logout, AuthApi.email/phone/oauth/siwe/siws/passkey sub-APIs
- Entrypoint: privy.auth.logout(), privy.auth.<method>
- Attacker controls: logout timing, userId passed to mfa.clearMfa, concurrent login calls
- Exploit idea: Call privy.auth.logout(), privy.auth.<method> with the mode field flipped and inspect which route and which session-update path executes.
- Invariant to test: The mode argument must never let a caller convert a link request into a session-issuing login inside src/client/auth/AuthApi.ts.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call AuthApi.logout with each accepted mode and assert updateWithTokensResponse is only reached for genuine login modes.
