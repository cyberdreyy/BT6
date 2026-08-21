# Q1663: redirect target chosen by caller in CustomProviderApi.ts

## Question
Can an attacker pass a redirect_to value into CustomProviderApi.syncWithToken that sends the authorization code to an origin they control while the SDK still treats the resulting callback as trusted?

## Target
- File/function: [src/client/auth/CustomProviderApi.ts](src/client/auth/CustomProviderApi.ts) - CustomProviderApi.syncWithToken, linkWithToken
- Entrypoint: privy.auth.customProvider.syncWithToken(token, opts, mode)
- Attacker controls: the third-party JWT string, mode, opts.embedded
- Exploit idea: Call generateURL with an attacker origin and complete loginWithCode with the code delivered there.
- Invariant to test: src/client/auth/CustomProviderApi.ts must not accept a redirect target that is unrelated to the app's configured origins.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call CustomProviderApi.syncWithToken with an off-origin redirect_to and assert the request is rejected client-side.
