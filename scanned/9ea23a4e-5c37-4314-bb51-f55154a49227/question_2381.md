# Q2381: message parameter order differs by method in loginWithCrossAppAuth.ts

## Question
crossApp signMessage sends params [message, address] while signTypedData sends [address, typedData]; can an attacker exploit an ordering mismatch through loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}` so the provider signs with the wrong account or over the wrong data?

## Target
- File/function: [src/action/crossApp/loginWithCrossAppAuth.ts](src/action/crossApp/loginWithCrossAppAuth.ts) - loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}`, redirectUrl) -> openAuthSession -> oauth.loginWithCode -> crossApp.updateOnCrossAppAuthentication
- Entrypoint: privy.crossApp.loginWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId string, redirectUrl, the privy_oauth_state / privy_oauth_code values returned by the auth session
- Exploit idea: Submit requests where message and address are both address-shaped strings.
- Invariant to test: Parameter binding must be explicit and type-checked.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit ambiguous params through loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}` and assert explicit binding.
