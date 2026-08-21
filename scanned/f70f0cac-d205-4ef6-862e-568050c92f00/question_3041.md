# Q3041: connections list fetched per request in loginWithCrossAppAuth.ts

## Question
getCrossAppConnections is fetched on each wallet action; can an attacker cause the list to change between the resolution and the request in loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}` so the token is sent to a different provider than the one authorised?

## Target
- File/function: [src/action/crossApp/loginWithCrossAppAuth.ts](src/action/crossApp/loginWithCrossAppAuth.ts) - loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}`, redirectUrl) -> openAuthSession -> oauth.loginWithCode -> crossApp.updateOnCrossAppAuthentication
- Entrypoint: privy.crossApp.loginWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId string, redirectUrl, the privy_oauth_state / privy_oauth_code values returned by the auth session
- Exploit idea: Change the connections response between the two awaits.
- Invariant to test: Provider identity must be pinned for the duration of an operation.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Integration test: swap the connections mid-call in loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}` and assert abort.
