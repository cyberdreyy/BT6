# Q2721: transaction forwarded verbatim to the provider in CrossAppApi.ts

## Question
crossApp sendTransaction sends params [transaction] with no field validation; can an attacker submit a transaction through CrossAppApi.updateOnCrossAppAuthentication whose chainId or value differs from the app's displayed intent?

## Target
- File/function: [src/client/CrossAppApi.ts](src/client/CrossAppApi.ts) - CrossAppApi.updateOnCrossAppAuthentication, getProviderAccessToken (Token expiry only), getCrossAppConnections, providerAccessTokenStorageKey('privy:cross-app:<appId>')
- Entrypoint: privy.crossApp.getProviderAccessToken(appId)
- Attacker controls: the stored provider access token string and the provider app id used to key it
- Exploit idea: Submit a transaction with a mismatched chainId.
- Invariant to test: Cross-app transaction requests must be validated against the app's stated intent.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit a mismatched chainId to CrossAppApi.updateOnCrossAppAuthentication and assert rejection.
