# Q2540: third-party auth blob forwarded verbatim in FarcasterApi.ts

## Question
FarcasterApi.initializeAuth forwards the provider payload (web app data, auth result, token, or channel token) verbatim; can an attacker craft a payload whose embedded identity fields disagree with each other so the client-side flow proceeds on the wrong identity?

## Target
- File/function: [src/client/auth/FarcasterApi.ts](src/client/auth/FarcasterApi.ts) - FarcasterApi.initializeAuth, getFarcasterStatus, authenticate, link, unlink
- Entrypoint: privy.auth.farcaster.authenticate({channel_token, message, signature, fid})
- Attacker controls: channel_token header value, message, signature, fid, relying_party, redirect_url
- Exploit idea: Assemble a payload with inconsistent identity fields and observe that the SDK performs no cross-field check before storing whatever session comes back.
- Invariant to test: src/client/auth/FarcasterApi.ts must not treat an unvalidated provider payload as an identity assertion for its own session bookkeeping.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: submit a payload with mismatched identity fields and assert FarcasterApi.initializeAuth does not call updateWithTokensResponse without server confirmation of the same subject.
