# Q3821: smart wallet detection scans every account in CrossAppApi.ts

## Question
isCrossAppWalletSmart flatMaps smart_wallets across all cross_app accounts; can an attacker add an account containing the victim's address so CrossAppApi.updateOnCrossAppAuthentication switches the signing method for a wallet they do not own?

## Target
- File/function: [src/client/CrossAppApi.ts](src/client/CrossAppApi.ts) - CrossAppApi.updateOnCrossAppAuthentication, getProviderAccessToken (Token expiry only), getCrossAppConnections, providerAccessTokenStorageKey('privy:cross-app:<appId>')
- Entrypoint: privy.crossApp.getProviderAccessToken(appId)
- Attacker controls: the stored provider access token string and the provider app id used to key it
- Exploit idea: Link an account listing the victim's address as a smart wallet.
- Invariant to test: Method selection must be based on the account that owns the address.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: add a decoy account and assert CrossAppApi.updateOnCrossAppAuthentication resolves ownership first.
