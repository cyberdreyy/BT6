# Q3964: no expiry in the signed statement in OAuthApi.ts

## Question
The statement built in src/client/auth/OAuthApi.ts carries Issued At but no expiration; can an attacker replay a signature captured months earlier through OAuthApi.generateURL?

## Target
- File/function: [src/client/auth/OAuthApi.ts](src/client/auth/OAuthApi.ts) - OAuthApi.generateURL, loginWithCode, linkWithCode, unlink
- Entrypoint: privy.auth.oauth.generateURL(provider, redirectTo) then loginWithCode(code, state, provider)
- Attacker controls: redirect_to URL, returned authorization_code and state_code, provider string, concurrent flows
- Exploit idea: Sign once, store the message and signature, replay after a long delay.
- Invariant to test: Authentication statements must carry an expiry the client enforces.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: assert OAuthApi.generateURL rejects a message whose Issued At is older than a short window.
