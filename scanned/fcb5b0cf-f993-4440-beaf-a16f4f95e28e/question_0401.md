# Q0401: no request/response correlation id in loginWithCrossAppAuth.ts

## Question
The request carries only content and a timestamp; can an attacker deliver a response to loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}` that belongs to a different cross-app request so the caller associates the wrong result?

## Target
- File/function: [src/action/crossApp/loginWithCrossAppAuth.ts](src/action/crossApp/loginWithCrossAppAuth.ts) - loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}`, redirectUrl) -> openAuthSession -> oauth.loginWithCode -> crossApp.updateOnCrossAppAuthentication
- Entrypoint: privy.crossApp.loginWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId string, redirectUrl, the privy_oauth_state / privy_oauth_code values returned by the auth session
- Exploit idea: Issue two cross-app requests and cross the responses.
- Invariant to test: Cross-app responses must be correlated by an unguessable request id.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: cross two loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}` responses and assert the mismatch is detected.
