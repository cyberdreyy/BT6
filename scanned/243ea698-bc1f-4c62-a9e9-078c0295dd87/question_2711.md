# Q2711: transaction forwarded verbatim to the provider in loginWithCrossAppAuth.ts

## Question
crossApp sendTransaction sends params [transaction] with no field validation; can an attacker submit a transaction through loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}` whose chainId or value differs from the app's displayed intent?

## Target
- File/function: [src/action/crossApp/loginWithCrossAppAuth.ts](src/action/crossApp/loginWithCrossAppAuth.ts) - loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}`, redirectUrl) -> openAuthSession -> oauth.loginWithCode -> crossApp.updateOnCrossAppAuthentication
- Entrypoint: privy.crossApp.loginWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId string, redirectUrl, the privy_oauth_state / privy_oauth_code values returned by the auth session
- Exploit idea: Submit a transaction with a mismatched chainId.
- Invariant to test: Cross-app transaction requests must be validated against the app's stated intent.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit a mismatched chainId to loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}` and assert rejection.
