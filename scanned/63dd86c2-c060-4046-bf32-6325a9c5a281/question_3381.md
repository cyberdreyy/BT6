# Q3381: communicationMode fixed to redirect in CrossAppApi.ts

## Question
The transact URL pins communicationMode=redirect; can an attacker exploit the redirect mode through CrossAppApi.updateOnCrossAppAuthentication so credentials or results traverse the browser address bar where other parties observe them?

## Target
- File/function: [src/client/CrossAppApi.ts](src/client/CrossAppApi.ts) - CrossAppApi.updateOnCrossAppAuthentication, getProviderAccessToken (Token expiry only), getCrossAppConnections, providerAccessTokenStorageKey('privy:cross-app:<appId>')
- Entrypoint: privy.crossApp.getProviderAccessToken(appId)
- Attacker controls: the stored provider access token string and the provider app id used to key it
- Exploit idea: Trace what appears in the address bar and referrer during the flow.
- Invariant to test: Sensitive cross-app material must not traverse navigable URLs.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert CrossAppApi.updateOnCrossAppAuthentication carries the token out-of-band rather than in the navigation.
