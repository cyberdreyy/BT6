# Q0961: storage key namespaced only by provider app id in CrossAppApi.ts

## Question
The cache key is privy:cross-app:<providerAppId>; can an attacker use a providerAppId string through CrossAppApi.updateOnCrossAppAuthentication that collides with another key namespace or with a different app's entry?

## Target
- File/function: [src/client/CrossAppApi.ts](src/client/CrossAppApi.ts) - CrossAppApi.updateOnCrossAppAuthentication, getProviderAccessToken (Token expiry only), getCrossAppConnections, providerAccessTokenStorageKey('privy:cross-app:<appId>')
- Entrypoint: privy.crossApp.getProviderAccessToken(appId)
- Attacker controls: the stored provider access token string and the provider app id used to key it
- Exploit idea: Pass a providerAppId containing ':' or matching another key prefix.
- Invariant to test: Storage keys must be injective over provider app ids.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass separator-bearing provider ids to CrossAppApi.updateOnCrossAppAuthentication and assert distinct keys.
