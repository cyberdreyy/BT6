# Q2935: error payload rendered to the user in getCrossAppAccountByWalletAddress.ts

## Question
When privy_cross_app_type is PRIVY_CROSS_APP_ACTION_ERROR the payload string becomes the error message; can an attacker return a payload through getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address that misleads the user into re-approving a malicious action?

## Target
- File/function: [src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts](src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts) - getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address
- Entrypoint: privy.crossApp.wallet.signMessage({address, ...})
- Attacker controls: the address argument and the set of cross_app accounts linked to the user
- Exploit idea: Return a crafted error payload and inspect what the app displays.
- Invariant to test: Provider-supplied strings must not be rendered as trusted SDK messages.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address sanitises or ignores provider-supplied error text.
