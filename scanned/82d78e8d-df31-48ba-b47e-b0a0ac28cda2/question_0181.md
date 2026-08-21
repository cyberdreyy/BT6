# Q0181: provider api url comes from the connections list in loginWithCrossAppAuth.ts

## Question
The transact URL host is provider_app_custom_api_url taken from the getCrossAppConnections response; can an attacker influence that value so loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}` sends the provider access token and the request payload to a host of their choosing?

## Target
- File/function: [src/action/crossApp/loginWithCrossAppAuth.ts](src/action/crossApp/loginWithCrossAppAuth.ts) - loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}`, redirectUrl) -> openAuthSession -> oauth.loginWithCode -> crossApp.updateOnCrossAppAuthentication
- Entrypoint: privy.crossApp.loginWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId string, redirectUrl, the privy_oauth_state / privy_oauth_code values returned by the auth session
- Exploit idea: Return a connections entry with an attacker host and observe the outbound request.
- Invariant to test: Cross-app endpoints must be validated against a trusted registry before credentials are attached.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: return a crafted provider_app_custom_api_url and assert loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}` refuses to send the token.
