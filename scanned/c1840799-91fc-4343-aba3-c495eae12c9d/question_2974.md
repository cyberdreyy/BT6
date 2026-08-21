# Q2974: error path leaves tokens but no user in OAuthApi.ts

## Question
When the post-login wallet creation step throws, does OAuthApi.generateURL leave the freshly stored tokens in place while never invoking setUser, leaving a live session the app believes does not exist?

## Target
- File/function: [src/client/auth/OAuthApi.ts](src/client/auth/OAuthApi.ts) - OAuthApi.generateURL, loginWithCode, linkWithCode, unlink
- Entrypoint: privy.auth.oauth.generateURL(provider, redirectTo) then loginWithCode(code, state, provider)
- Attacker controls: redirect_to URL, returned authorization_code and state_code, provider string, concurrent flows
- Exploit idea: Force maybeCreateWalletOnLogin to reject and inspect storage and the app callback.
- Invariant to test: A login that does not complete must not leave usable credentials behind.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: make the create step reject and assert storage holds no privy:token afterwards.
