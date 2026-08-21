# Q3973: no expiry in the signed statement in CustomProviderApi.ts

## Question
The statement built in src/client/auth/CustomProviderApi.ts carries Issued At but no expiration; can an attacker replay a signature captured months earlier through CustomProviderApi.syncWithToken?

## Target
- File/function: [src/client/auth/CustomProviderApi.ts](src/client/auth/CustomProviderApi.ts) - CustomProviderApi.syncWithToken, linkWithToken
- Entrypoint: privy.auth.customProvider.syncWithToken(token, opts, mode)
- Attacker controls: the third-party JWT string, mode, opts.embedded
- Exploit idea: Sign once, store the message and signature, replay after a long delay.
- Invariant to test: Authentication statements must carry an expiry the client enforces.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: assert CustomProviderApi.syncWithToken rejects a message whose Issued At is older than a short window.
