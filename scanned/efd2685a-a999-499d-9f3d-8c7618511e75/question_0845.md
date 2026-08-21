# Q0845: cached token validated only by decoded expiry in getCrossAppAccountByWalletAddress.ts

## Question
getProviderAccessToken parses the stored string with the unverified Token wrapper and only checks expiry; can an attacker place a self-issued JWT under that key so getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address treats it as a valid provider token?

## Target
- File/function: [src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts](src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts) - getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address
- Entrypoint: privy.crossApp.wallet.signMessage({address, ...})
- Attacker controls: the address argument and the set of cross_app accounts linked to the user
- Exploit idea: Write a crafted JWT with a distant exp under the storage key and trigger a cross-app action.
- Invariant to test: Cached credentials must be validated for provenance, not merely for expiry.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: seed a crafted JWT and assert getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address refuses to use it.
