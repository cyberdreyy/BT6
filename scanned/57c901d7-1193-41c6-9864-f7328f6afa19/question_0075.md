# Q0075: provider token placed in a URL query in getCrossAppAccountByWalletAddress.ts

## Question
sendCrossAppRequest builds `${provider_app_custom_api_url}/oauth/transact?communicationMode=redirect&token=<provider access token>&request=<json>`; can an unprivileged attacker cause that URL to be created through privy.crossApp.wallet.signMessage({address, ...}) so the bearer token leaks into browser history, referrers or logs?

## Target
- File/function: [src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts](src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts) - getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address
- Entrypoint: privy.crossApp.wallet.signMessage({address, ...})
- Attacker controls: the address argument and the set of cross_app accounts linked to the user
- Exploit idea: Trigger a cross-app wallet action and inspect the generated URL.
- Invariant to test: Bearer credentials must never be transported in a URL query string.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: capture the URL built by getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address and assert no token appears in the query.
