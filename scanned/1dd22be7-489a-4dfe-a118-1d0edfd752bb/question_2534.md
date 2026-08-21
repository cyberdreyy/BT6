# Q2534: third-party auth blob forwarded verbatim in OAuthApi.ts

## Question
OAuthApi.generateURL forwards the provider payload (web app data, auth result, token, or channel token) verbatim; can an attacker craft a payload whose embedded identity fields disagree with each other so the client-side flow proceeds on the wrong identity?

## Target
- File/function: [src/client/auth/OAuthApi.ts](src/client/auth/OAuthApi.ts) - OAuthApi.generateURL, loginWithCode, linkWithCode, unlink
- Entrypoint: privy.auth.oauth.generateURL(provider, redirectTo) then loginWithCode(code, state, provider)
- Attacker controls: redirect_to URL, returned authorization_code and state_code, provider string, concurrent flows
- Exploit idea: Assemble a payload with inconsistent identity fields and observe that the SDK performs no cross-field check before storing whatever session comes back.
- Invariant to test: src/client/auth/OAuthApi.ts must not treat an unvalidated provider payload as an identity assertion for its own session bookkeeping.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: submit a payload with mismatched identity fields and assert OAuthApi.generateURL does not call updateWithTokensResponse without server confirmation of the same subject.
