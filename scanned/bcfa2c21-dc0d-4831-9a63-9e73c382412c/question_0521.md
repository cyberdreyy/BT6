# Q0521: timestamp not validated on return in CrossAppApi.ts

## Question
The request payload contains Date.now() but nothing verifies it on the way back; can an attacker replay an old cross-app response into CrossAppApi.updateOnCrossAppAuthentication?

## Target
- File/function: [src/client/CrossAppApi.ts](src/client/CrossAppApi.ts) - CrossAppApi.updateOnCrossAppAuthentication, getProviderAccessToken (Token expiry only), getCrossAppConnections, providerAccessTokenStorageKey('privy:cross-app:<appId>')
- Entrypoint: privy.crossApp.getProviderAccessToken(appId)
- Attacker controls: the stored provider access token string and the provider app id used to key it
- Exploit idea: Capture a response and replay it for a later request.
- Invariant to test: Cross-app responses must be fresh and single-use.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: replay a captured response into CrossAppApi.updateOnCrossAppAuthentication and assert rejection.
