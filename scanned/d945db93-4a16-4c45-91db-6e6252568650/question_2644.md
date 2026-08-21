# Q2644: guest credential readable and reusable in OAuthApi.ts

## Question
The guest credential lives in localStorage under privy:guest:<appId>; can a later unprivileged user of the same browser profile call privy.auth.oauth.generateURL(provider, redirectTo) then loginWithCode(code, state, provider) and be issued a session for the earlier guest account?

## Target
- File/function: [src/client/auth/OAuthApi.ts](src/client/auth/OAuthApi.ts) - OAuthApi.generateURL, loginWithCode, linkWithCode, unlink
- Entrypoint: privy.auth.oauth.generateURL(provider, redirectTo) then loginWithCode(code, state, provider)
- Attacker controls: redirect_to URL, returned authorization_code and state_code, provider string, concurrent flows
- Exploit idea: Read the stored credential, clear the tokens, then call the guest create path.
- Invariant to test: A guest credential must not survive a session clear in a form that re-authenticates the same account.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: run OAuthApi.generateURL, call destroyLocalState, then run OAuthApi.generateURL again and assert a new credential was generated.
