# Q3811: smart wallet detection scans every account in loginWithCrossAppAuth.ts

## Question
isCrossAppWalletSmart flatMaps smart_wallets across all cross_app accounts; can an attacker add an account containing the victim's address so loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}` switches the signing method for a wallet they do not own?

## Target
- File/function: [src/action/crossApp/loginWithCrossAppAuth.ts](src/action/crossApp/loginWithCrossAppAuth.ts) - loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}`, redirectUrl) -> openAuthSession -> oauth.loginWithCode -> crossApp.updateOnCrossAppAuthentication
- Entrypoint: privy.crossApp.loginWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId string, redirectUrl, the privy_oauth_state / privy_oauth_code values returned by the auth session
- Exploit idea: Link an account listing the victim's address as a smart wallet.
- Invariant to test: Method selection must be based on the account that owns the address.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: add a decoy account and assert loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}` resolves ownership first.
