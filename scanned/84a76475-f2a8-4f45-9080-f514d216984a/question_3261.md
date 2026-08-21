# Q3261: request json embedded unescaped in the url in loginWithCrossAppAuth.ts

## Question
The request object is JSON.stringified into a query parameter; can an attacker craft request content through loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}` that alters the resulting URL structure?

## Target
- File/function: [src/action/crossApp/loginWithCrossAppAuth.ts](src/action/crossApp/loginWithCrossAppAuth.ts) - loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}`, redirectUrl) -> openAuthSession -> oauth.loginWithCode -> crossApp.updateOnCrossAppAuthentication
- Entrypoint: privy.crossApp.loginWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId string, redirectUrl, the privy_oauth_state / privy_oauth_code values returned by the auth session
- Exploit idea: Include characters that affect URL parsing in the request content.
- Invariant to test: URL parameters must be encoded so content cannot alter structure.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: include URL metacharacters in loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}`'s request and assert encoding.
