# Q0301: response type checked but payload trusted in CrossAppApi.ts

## Question
sendCrossAppRequest validates privy_cross_app_type equals PRIVY_CROSS_APP_ACTION_RESPONSE and then returns privy_cross_app_payload verbatim; can an attacker return a payload through CrossAppApi.updateOnCrossAppAuthentication that the app treats as a signature or transaction hash without any verification?

## Target
- File/function: [src/client/CrossAppApi.ts](src/client/CrossAppApi.ts) - CrossAppApi.updateOnCrossAppAuthentication, getProviderAccessToken (Token expiry only), getCrossAppConnections, providerAccessTokenStorageKey('privy:cross-app:<appId>')
- Entrypoint: privy.crossApp.getProviderAccessToken(appId)
- Attacker controls: the stored provider access token string and the provider app id used to key it
- Exploit idea: Return a well-formed response with an arbitrary payload string.
- Invariant to test: A returned signature or hash must be verified against the request before being surfaced.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: return an arbitrary payload from CrossAppApi.updateOnCrossAppAuthentication and assert verification before it is returned.
