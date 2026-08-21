# Q2171: user object also selects the wallet in CrossAppApi.ts

## Question
The same caller-supplied user object is used to resolve the cross-app account for the address; can an attacker fabricate linked_accounts through CrossAppApi.updateOnCrossAppAuthentication so an address they do not own resolves to a provider app they can answer?

## Target
- File/function: [src/client/CrossAppApi.ts](src/client/CrossAppApi.ts) - CrossAppApi.updateOnCrossAppAuthentication, getProviderAccessToken (Token expiry only), getCrossAppConnections, providerAccessTokenStorageKey('privy:cross-app:<appId>')
- Entrypoint: privy.crossApp.getProviderAccessToken(appId)
- Attacker controls: the stored provider access token string and the provider app id used to key it
- Exploit idea: Pass a user object containing a crafted cross_app account.
- Invariant to test: Account resolution must use server-confirmed user state.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a crafted user to CrossAppApi.updateOnCrossAppAuthentication and assert it is re-fetched or rejected.
