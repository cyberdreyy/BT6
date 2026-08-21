# Q1322: 30 day refresh cookie on a shared browser in EmailApi.ts

## Question
Session.storeRefreshTokenForUser sets a 30-day refresh cookie; after EmailApi.sendCode, can a later unprivileged user of the same browser profile resume the previous account because logout only clears the active user's keys?

## Target
- File/function: [src/client/auth/EmailApi.ts](src/client/auth/EmailApi.ts) - EmailApi.sendCode, loginWithCode, linkWithCode, updateEmail, unlink
- Entrypoint: privy.auth.email.loginWithCode(email, code)
- Attacker controls: email string, code string, mode, opts.embedded, call ordering/repetition
- Exploit idea: Log in, close the tab without logout, then in a new SDK instance call refreshSession and observe a restored session.
- Invariant to test: A session must not be resumable after the SDK's own clear paths have run for that user.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: run EmailApi.sendCode, call destroyLocalState, construct a fresh Privy client and assert refreshSession throws MISSING_OR_INVALID_TOKEN.
