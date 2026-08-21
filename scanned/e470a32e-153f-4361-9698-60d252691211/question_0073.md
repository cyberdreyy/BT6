# Q0073: provider token placed in a URL query in sendCrossAppRequest.ts

## Question
sendCrossAppRequest builds `${provider_app_custom_api_url}/oauth/transact?communicationMode=redirect&token=<provider access token>&request=<json>`; can an unprivileged attacker cause that URL to be created through any privy.crossApp.wallet.* call so the bearer token leaks into browser history, referrers or logs?

## Target
- File/function: [src/action/crossApp/wallet/utils/sendCrossAppRequest.ts](src/action/crossApp/wallet/utils/sendCrossAppRequest.ts) - sendCrossAppRequest: builds `${provider_app_custom_api_url}/oauth/transact?communicationMode=redirect&token=<accessToken>&request=<json>` then validates privy_cross_app_type
- Entrypoint: any privy.crossApp.wallet.* call
- Attacker controls: the request payload, callbackUrl, and the privy_cross_app_type / privy_cross_app_payload pair returned to the SDK
- Exploit idea: Trigger a cross-app wallet action and inspect the generated URL.
- Invariant to test: Bearer credentials must never be transported in a URL query string.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: capture the URL built by sendCrossAppRequest: builds `${provider_app_custom_api_url}/oauth/transact?communicationMode=redirect&token=<accessToken>&request=<json>` then validates privy_cross_app_type and assert no token appears in the query.
