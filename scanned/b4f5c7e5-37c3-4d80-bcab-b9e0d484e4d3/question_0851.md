# Q0851: cached token validated only by decoded expiry in CrossAppApi.ts

## Question
getProviderAccessToken parses the stored string with the unverified Token wrapper and only checks expiry; can an attacker place a self-issued JWT under that key so CrossAppApi.updateOnCrossAppAuthentication treats it as a valid provider token?

## Target
- File/function: [src/client/CrossAppApi.ts](src/client/CrossAppApi.ts) - CrossAppApi.updateOnCrossAppAuthentication, getProviderAccessToken (Token expiry only), getCrossAppConnections, providerAccessTokenStorageKey('privy:cross-app:<appId>')
- Entrypoint: privy.crossApp.getProviderAccessToken(appId)
- Attacker controls: the stored provider access token string and the provider app id used to key it
- Exploit idea: Write a crafted JWT with a distant exp under the storage key and trigger a cross-app action.
- Invariant to test: Cached credentials must be validated for provenance, not merely for expiry.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: seed a crafted JWT and assert CrossAppApi.updateOnCrossAppAuthentication refuses to use it.
