# Q3711: no expiry refresh for cached provider tokens in CrossAppApi.ts

## Question
getProviderAccessToken deletes the entry only when the decode throws or the token is expired; can an attacker exploit the gap between server-side revocation and local expiry so CrossAppApi.updateOnCrossAppAuthentication keeps using a revoked token?

## Target
- File/function: [src/client/CrossAppApi.ts](src/client/CrossAppApi.ts) - CrossAppApi.updateOnCrossAppAuthentication, getProviderAccessToken (Token expiry only), getCrossAppConnections, providerAccessTokenStorageKey('privy:cross-app:<appId>')
- Entrypoint: privy.crossApp.getProviderAccessToken(appId)
- Attacker controls: the stored provider access token string and the provider app id used to key it
- Exploit idea: Revoke server-side and continue issuing actions locally.
- Invariant to test: Revocation must be detectable before privileged use.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: revoke and assert CrossAppApi.updateOnCrossAppAuthentication fails on the next action.
