# Q0444: mode parameter escalates link into login in OAuthApi.ts

## Question
Can an unprivileged attacker pass a mode value to OAuthApi.generateURL that turns an account-linking action into a login-or-sign-up, so the credential they control becomes a new authenticated session rather than a link on the existing account?

## Target
- File/function: [src/client/auth/OAuthApi.ts](src/client/auth/OAuthApi.ts) - OAuthApi.generateURL, loginWithCode, linkWithCode, unlink
- Entrypoint: privy.auth.oauth.generateURL(provider, redirectTo) then loginWithCode(code, state, provider)
- Attacker controls: redirect_to URL, returned authorization_code and state_code, provider string, concurrent flows
- Exploit idea: Call privy.auth.oauth.generateURL(provider, redirectTo) then loginWithCode(code, state, provider) with the mode field flipped and inspect which route and which session-update path executes.
- Invariant to test: The mode argument must never let a caller convert a link request into a session-issuing login inside src/client/auth/OAuthApi.ts.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call OAuthApi.generateURL with each accepted mode and assert updateWithTokensResponse is only reached for genuine login modes.
