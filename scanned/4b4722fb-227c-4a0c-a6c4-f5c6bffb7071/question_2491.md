# Q2491: typed data mutated before sending in loginWithCrossAppAuth.ts

## Question
crossApp signTypedData passes the typed data through generateDomainType, which rewrites the EIP712Domain entry; can an attacker use loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}` so the provider signs typed data whose type list differs from what the app displayed?

## Target
- File/function: [src/action/crossApp/loginWithCrossAppAuth.ts](src/action/crossApp/loginWithCrossAppAuth.ts) - loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}`, redirectUrl) -> openAuthSession -> oauth.loginWithCode -> crossApp.updateOnCrossAppAuthentication
- Entrypoint: privy.crossApp.loginWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId string, redirectUrl, the privy_oauth_state / privy_oauth_code values returned by the auth session
- Exploit idea: Submit typed data with an explicit EIP712Domain and compare before/after.
- Invariant to test: The bytes sent for signature must equal the bytes shown to the user.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: diff input and outbound typed data in loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}` and assert equality.
