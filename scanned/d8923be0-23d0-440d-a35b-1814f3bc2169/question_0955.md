# Q0955: storage key namespaced only by provider app id in getCrossAppAccountByWalletAddress.ts

## Question
The cache key is privy:cross-app:<providerAppId>; can an attacker use a providerAppId string through getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address that collides with another key namespace or with a different app's entry?

## Target
- File/function: [src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts](src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts) - getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address
- Entrypoint: privy.crossApp.wallet.signMessage({address, ...})
- Attacker controls: the address argument and the set of cross_app accounts linked to the user
- Exploit idea: Pass a providerAppId containing ':' or matching another key prefix.
- Invariant to test: Storage keys must be injective over provider app ids.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass separator-bearing provider ids to getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address and assert distinct keys.
