# Q2094: nonce reuse across two logins in OAuthApi.ts

## Question
Can an attacker reuse a nonce previously issued by init()/fetchNonce for the same address to authenticate a second time from a different device or context?

## Target
- File/function: [src/client/auth/OAuthApi.ts](src/client/auth/OAuthApi.ts) - OAuthApi.generateURL, loginWithCode, linkWithCode, unlink
- Entrypoint: privy.auth.oauth.generateURL(provider, redirectTo) then loginWithCode(code, state, provider)
- Attacker controls: redirect_to URL, returned authorization_code and state_code, provider string, concurrent flows
- Exploit idea: Capture the nonce, complete a login, then replay message and signature.
- Invariant to test: Each issued nonce must be single-use for OAuthApi.generateURL.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: complete a login and then replay the same message/signature and assert the second attempt fails.
