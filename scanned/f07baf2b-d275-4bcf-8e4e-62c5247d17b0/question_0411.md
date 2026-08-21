# Q0411: no request/response correlation id in CrossAppApi.ts

## Question
The request carries only content and a timestamp; can an attacker deliver a response to CrossAppApi.updateOnCrossAppAuthentication that belongs to a different cross-app request so the caller associates the wrong result?

## Target
- File/function: [src/client/CrossAppApi.ts](src/client/CrossAppApi.ts) - CrossAppApi.updateOnCrossAppAuthentication, getProviderAccessToken (Token expiry only), getCrossAppConnections, providerAccessTokenStorageKey('privy:cross-app:<appId>')
- Entrypoint: privy.crossApp.getProviderAccessToken(appId)
- Attacker controls: the stored provider access token string and the provider app id used to key it
- Exploit idea: Issue two cross-app requests and cross the responses.
- Invariant to test: Cross-app responses must be correlated by an unguessable request id.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: cross two CrossAppApi.updateOnCrossAppAuthentication responses and assert the mismatch is detected.
