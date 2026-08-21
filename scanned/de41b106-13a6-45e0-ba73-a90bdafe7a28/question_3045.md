# Q3045: connections list fetched per request in getCrossAppAccountByWalletAddress.ts

## Question
getCrossAppConnections is fetched on each wallet action; can an attacker cause the list to change between the resolution and the request in getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address so the token is sent to a different provider than the one authorised?

## Target
- File/function: [src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts](src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts) - getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address
- Entrypoint: privy.crossApp.wallet.signMessage({address, ...})
- Attacker controls: the address argument and the set of cross_app accounts linked to the user
- Exploit idea: Change the connections response between the two awaits.
- Invariant to test: Provider identity must be pinned for the duration of an operation.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Integration test: swap the connections mid-call in getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address and assert abort.
