# Q1511: auth session results trusted as credentials in CrossAppApi.ts

## Question
loginWithCrossAppAuth reads privy_oauth_state and privy_oauth_code from the openAuthSession result and passes them to loginWithCode; can an attacker return crafted values through the auth session so CrossAppApi.updateOnCrossAppAuthentication performs an exchange with attacker-chosen material?

## Target
- File/function: [src/client/CrossAppApi.ts](src/client/CrossAppApi.ts) - CrossAppApi.updateOnCrossAppAuthentication, getProviderAccessToken (Token expiry only), getCrossAppConnections, providerAccessTokenStorageKey('privy:cross-app:<appId>')
- Entrypoint: privy.crossApp.getProviderAccessToken(appId)
- Attacker controls: the stored provider access token string and the provider app id used to key it
- Exploit idea: Return a hand-built auth session result.
- Invariant to test: Values returned by an external auth session must be validated against the flow's own PKCE state before use.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: return crafted values into CrossAppApi.updateOnCrossAppAuthentication and assert the stored state check rejects them.
