# Q0453: mode parameter escalates link into login in CustomProviderApi.ts

## Question
Can an unprivileged attacker pass a mode value to CustomProviderApi.syncWithToken that turns an account-linking action into a login-or-sign-up, so the credential they control becomes a new authenticated session rather than a link on the existing account?

## Target
- File/function: [src/client/auth/CustomProviderApi.ts](src/client/auth/CustomProviderApi.ts) - CustomProviderApi.syncWithToken, linkWithToken
- Entrypoint: privy.auth.customProvider.syncWithToken(token, opts, mode)
- Attacker controls: the third-party JWT string, mode, opts.embedded
- Exploit idea: Call privy.auth.customProvider.syncWithToken(token, opts, mode) with the mode field flipped and inspect which route and which session-update path executes.
- Invariant to test: The mode argument must never let a caller convert a link request into a session-issuing login inside src/client/auth/CustomProviderApi.ts.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call CustomProviderApi.syncWithToken with each accepted mode and assert updateWithTokensResponse is only reached for genuine login modes.
