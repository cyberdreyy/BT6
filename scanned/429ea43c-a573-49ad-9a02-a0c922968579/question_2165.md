# Q2165: user object also selects the wallet in getCrossAppAccountByWalletAddress.ts

## Question
The same caller-supplied user object is used to resolve the cross-app account for the address; can an attacker fabricate linked_accounts through getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address so an address they do not own resolves to a provider app they can answer?

## Target
- File/function: [src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts](src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts) - getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address
- Entrypoint: privy.crossApp.wallet.signMessage({address, ...})
- Attacker controls: the address argument and the set of cross_app accounts linked to the user
- Exploit idea: Pass a user object containing a crafted cross_app account.
- Invariant to test: Account resolution must use server-confirmed user state.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a crafted user to getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address and assert it is re-fetched or rejected.
