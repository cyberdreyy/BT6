# Q0185: provider api url comes from the connections list in getCrossAppAccountByWalletAddress.ts

## Question
The transact URL host is provider_app_custom_api_url taken from the getCrossAppConnections response; can an attacker influence that value so getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address sends the provider access token and the request payload to a host of their choosing?

## Target
- File/function: [src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts](src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts) - getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address
- Entrypoint: privy.crossApp.wallet.signMessage({address, ...})
- Attacker controls: the address argument and the set of cross_app accounts linked to the user
- Exploit idea: Return a connections entry with an attacker host and observe the outbound request.
- Invariant to test: Cross-app endpoints must be validated against a trusted registry before credentials are attached.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: return a crafted provider_app_custom_api_url and assert getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address refuses to send the token.
