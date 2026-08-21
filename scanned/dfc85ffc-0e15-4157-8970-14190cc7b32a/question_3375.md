# Q3375: communicationMode fixed to redirect in getCrossAppAccountByWalletAddress.ts

## Question
The transact URL pins communicationMode=redirect; can an attacker exploit the redirect mode through getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address so credentials or results traverse the browser address bar where other parties observe them?

## Target
- File/function: [src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts](src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts) - getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address
- Entrypoint: privy.crossApp.wallet.signMessage({address, ...})
- Attacker controls: the address argument and the set of cross_app accounts linked to the user
- Exploit idea: Trace what appears in the address bar and referrer during the flow.
- Invariant to test: Sensitive cross-app material must not traverse navigable URLs.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address carries the token out-of-band rather than in the navigation.
