# Q1883: domain and uri are caller-controlled in CustomProviderApi.ts

## Question
CustomProviderApi.syncWithToken builds the signing statement from a caller-supplied domain and uri; can an attacker present a message whose domain names a different application so a signature harvested elsewhere authenticates here?

## Target
- File/function: [src/client/auth/CustomProviderApi.ts](src/client/auth/CustomProviderApi.ts) - CustomProviderApi.syncWithToken, linkWithToken
- Entrypoint: privy.auth.customProvider.syncWithToken(token, opts, mode)
- Attacker controls: the third-party JWT string, mode, opts.embedded
- Exploit idea: Build a message with the victim app's domain, obtain a signature in another context, and submit it.
- Invariant to test: The signed statement must be bound to the origin actually performing the authentication.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: assert CustomProviderApi.syncWithToken rejects a domain that does not match the configured app origin.
