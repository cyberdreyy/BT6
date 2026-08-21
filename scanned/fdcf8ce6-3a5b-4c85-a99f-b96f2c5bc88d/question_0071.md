# Q0071: provider token placed in a URL query in loginWithCrossAppAuth.ts

## Question
sendCrossAppRequest builds `${provider_app_custom_api_url}/oauth/transact?communicationMode=redirect&token=<provider access token>&request=<json>`; can an unprivileged attacker cause that URL to be created through privy.crossApp.loginWithCrossAppAuth({providerAppId, redirectUrl}) so the bearer token leaks into browser history, referrers or logs?

## Target
- File/function: [src/action/crossApp/loginWithCrossAppAuth.ts](src/action/crossApp/loginWithCrossAppAuth.ts) - loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}`, redirectUrl) -> openAuthSession -> oauth.loginWithCode -> crossApp.updateOnCrossAppAuthentication
- Entrypoint: privy.crossApp.loginWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId string, redirectUrl, the privy_oauth_state / privy_oauth_code values returned by the auth session
- Exploit idea: Trigger a cross-app wallet action and inspect the generated URL.
- Invariant to test: Bearer credentials must never be transported in a URL query string.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: capture the URL built by loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}` and assert no token appears in the query.
