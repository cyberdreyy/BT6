# Q1401: provider app id becomes an oauth provider string in CrossAppApi.ts

## Question
Cross-app auth calls oauth.generateURL with `privy:${providerAppId}`; can an attacker pass a providerAppId that produces a provider string the OAuth layer interprets differently through CrossAppApi.updateOnCrossAppAuthentication?

## Target
- File/function: [src/client/CrossAppApi.ts](src/client/CrossAppApi.ts) - CrossAppApi.updateOnCrossAppAuthentication, getProviderAccessToken (Token expiry only), getCrossAppConnections, providerAccessTokenStorageKey('privy:cross-app:<appId>')
- Entrypoint: privy.crossApp.getProviderAccessToken(appId)
- Attacker controls: the stored provider access token string and the provider app id used to key it
- Exploit idea: Pass ids containing ':' or known provider names.
- Invariant to test: Provider identifiers must be validated before being embedded in a provider string.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass crafted provider ids to CrossAppApi.updateOnCrossAppAuthentication and assert validation.
