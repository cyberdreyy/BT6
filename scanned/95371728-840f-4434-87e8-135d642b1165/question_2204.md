# Q2204: relying party string controlled by caller in OAuthApi.ts

## Question
In src/client/auth/OAuthApi.ts, is the relying party supplied by the caller and echoed into the ceremony, letting an attacker start a credential ceremony scoped to a different origin than the one they occupy?

## Target
- File/function: [src/client/auth/OAuthApi.ts](src/client/auth/OAuthApi.ts) - OAuthApi.generateURL, loginWithCode, linkWithCode, unlink
- Entrypoint: privy.auth.oauth.generateURL(provider, redirectTo) then loginWithCode(code, state, provider)
- Attacker controls: redirect_to URL, returned authorization_code and state_code, provider string, concurrent flows
- Exploit idea: Call OAuthApi.generateURL with a relying party that is not the current origin and observe the options returned.
- Invariant to test: The relying party used by OAuthApi.generateURL must be derived from the app's configured origin.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call OAuthApi.generateURL with a foreign relying party and assert the SDK refuses.
