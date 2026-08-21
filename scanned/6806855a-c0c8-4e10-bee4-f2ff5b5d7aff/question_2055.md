# Q2055: logged-in check uses the caller's user object in getCrossAppAccountByWalletAddress.ts

## Question
throwIfNotLoggedIn only inspects the user object handed in by the caller; can an attacker pass a fabricated user through privy.crossApp.wallet.signMessage({address, ...}) so getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address proceeds without a real session?

## Target
- File/function: [src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts](src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts) - getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address
- Entrypoint: privy.crossApp.wallet.signMessage({address, ...})
- Attacker controls: the address argument and the set of cross_app accounts linked to the user
- Exploit idea: Call the wallet action with a hand-built user object and no session.
- Invariant to test: Authorization checks must consult the session, not caller-supplied data.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address with a fabricated user and no tokens and assert refusal.
