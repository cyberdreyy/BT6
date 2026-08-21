# Q0625: callback url supplied by the caller in getCrossAppAccountByWalletAddress.ts

## Question
The callbackUrl and redirectUrl come from the caller; can an attacker set them through privy.crossApp.wallet.signMessage({address, ...}) so the cross-app result (and any credential in the redirect) is delivered to an origin they control?

## Target
- File/function: [src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts](src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts) - getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address
- Entrypoint: privy.crossApp.wallet.signMessage({address, ...})
- Attacker controls: the address argument and the set of cross_app accounts linked to the user
- Exploit idea: Call the action with an attacker-controlled redirectUrl.
- Invariant to test: Callback targets must be constrained to the app's configured origins.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: pass a foreign redirectUrl to getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address and assert rejection.
