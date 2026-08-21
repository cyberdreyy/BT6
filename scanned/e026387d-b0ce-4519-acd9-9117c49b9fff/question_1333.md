# Q1333: 30 day refresh cookie on a shared browser in CustomProviderApi.ts

## Question
Session.storeRefreshTokenForUser sets a 30-day refresh cookie; after CustomProviderApi.syncWithToken, can a later unprivileged user of the same browser profile resume the previous account because logout only clears the active user's keys?

## Target
- File/function: [src/client/auth/CustomProviderApi.ts](src/client/auth/CustomProviderApi.ts) - CustomProviderApi.syncWithToken, linkWithToken
- Entrypoint: privy.auth.customProvider.syncWithToken(token, opts, mode)
- Attacker controls: the third-party JWT string, mode, opts.embedded
- Exploit idea: Log in, close the tab without logout, then in a new SDK instance call refreshSession and observe a restored session.
- Invariant to test: A session must not be resumable after the SDK's own clear paths have run for that user.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: run CustomProviderApi.syncWithToken, call destroyLocalState, construct a fresh Privy client and assert refreshSession throws MISSING_OR_INVALID_TOKEN.
