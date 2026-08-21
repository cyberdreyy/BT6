# Q0078: provider token placed in a URL query in signMessage.ts

## Question
sendCrossAppRequest builds `${provider_app_custom_api_url}/oauth/transact?communicationMode=redirect&token=<provider access token>&request=<json>`; can an unprivileged attacker cause that URL to be created through privy.crossApp.wallet.signMessage({user, address, message, redirectUrl}) so the bearer token leaks into browser history, referrers or logs?

## Target
- File/function: [src/action/crossApp/wallet/signMessage.ts](src/action/crossApp/wallet/signMessage.ts) - crossApp signMessage: params [message, address], method chosen by isCrossAppWalletSmart
- Entrypoint: privy.crossApp.wallet.signMessage({user, address, message, redirectUrl})
- Attacker controls: message bytes/string, address, redirectUrl, provider response payload
- Exploit idea: Trigger a cross-app wallet action and inspect the generated URL.
- Invariant to test: Bearer credentials must never be transported in a URL query string.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: capture the URL built by crossApp signMessage: params [message and assert no token appears in the query.
