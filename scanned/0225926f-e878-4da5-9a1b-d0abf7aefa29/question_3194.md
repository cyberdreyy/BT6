# Q3194: clearMfa keyed by caller-supplied userId in OAuthApi.ts

## Question
AuthApi.logout forwards opts.userId to mfa.clearMfa; can an attacker pass another user's id and clear MFA state that is not theirs?

## Target
- File/function: [src/client/auth/OAuthApi.ts](src/client/auth/OAuthApi.ts) - OAuthApi.generateURL, loginWithCode, linkWithCode, unlink
- Entrypoint: privy.auth.oauth.generateURL(provider, redirectTo) then loginWithCode(code, state, provider)
- Attacker controls: redirect_to URL, returned authorization_code and state_code, provider string, concurrent flows
- Exploit idea: Call logout with a foreign userId and observe the proxy clearMfa invocation.
- Invariant to test: MFA state may only be cleared for the currently authenticated user.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call OAuthApi.generateURL with a foreign userId and assert clearMfa is called with the session's own user id or not at all.
