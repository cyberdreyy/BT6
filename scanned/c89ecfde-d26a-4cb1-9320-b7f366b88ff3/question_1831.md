# Q1831: address comparison is exact string equality in loginWithCrossAppAuth.ts

## Question
Address membership is tested by === without normalisation; can an attacker submit a checksummed or padded variant through loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}` so the account is not found, or a different account is selected?

## Target
- File/function: [src/action/crossApp/loginWithCrossAppAuth.ts](src/action/crossApp/loginWithCrossAppAuth.ts) - loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}`, redirectUrl) -> openAuthSession -> oauth.loginWithCode -> crossApp.updateOnCrossAppAuthentication
- Entrypoint: privy.crossApp.loginWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId string, redirectUrl, the privy_oauth_state / privy_oauth_code values returned by the auth session
- Exploit idea: Pass mixed-case and padded address variants.
- Invariant to test: Address comparison must be canonical.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: table-test address forms through loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}`.
