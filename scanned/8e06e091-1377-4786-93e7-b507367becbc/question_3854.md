# Q3854: uppercase or checksummed address mismatch in OAuthApi.ts

## Question
Can an attacker exploit address case handling in OAuthApi.generateURL so the address used for the nonce request differs textually from the address embedded in the signed message?

## Target
- File/function: [src/client/auth/OAuthApi.ts](src/client/auth/OAuthApi.ts) - OAuthApi.generateURL, loginWithCode, linkWithCode, unlink
- Entrypoint: privy.auth.oauth.generateURL(provider, redirectTo) then loginWithCode(code, state, provider)
- Attacker controls: redirect_to URL, returned authorization_code and state_code, provider string, concurrent flows
- Exploit idea: Request the nonce with a lowercase address and sign a checksummed variant.
- Invariant to test: Address comparison in src/client/auth/OAuthApi.ts must be canonical.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: feed mixed-case address pairs to OAuthApi.generateURL and assert consistent canonicalisation.
