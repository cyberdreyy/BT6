# Q0076: provider token placed in a URL query in isCrossAppWalletSmart.ts

## Question
sendCrossAppRequest builds `${provider_app_custom_api_url}/oauth/transact?communicationMode=redirect&token=<provider access token>&request=<json>`; can an unprivileged attacker cause that URL to be created through method selection between personal_sign and privy_signSmartWalletMessage so the bearer token leaks into browser history, referrers or logs?

## Target
- File/function: [src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts](src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts) - isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets
- Entrypoint: method selection between personal_sign and privy_signSmartWalletMessage
- Attacker controls: the address argument and duplicate addresses across accounts
- Exploit idea: Trigger a cross-app wallet action and inspect the generated URL.
- Invariant to test: Bearer credentials must never be transported in a URL query string.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: capture the URL built by isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets and assert no token appears in the query.
