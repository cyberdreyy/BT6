# Q0986: create-on-login policy evaluated client-side in getUserSmartWallet.ts

## Question
getUserSmartWallet: first linked account of type smart_wallet decides whether to provision a wallet from the createOnLogin setting and the user's existing accounts; can an attacker influence that evaluation so a wallet is created (or skipped) against the app's policy?

## Target
- File/function: [src/utils/getUserSmartWallet.ts](src/utils/getUserSmartWallet.ts) - getUserSmartWallet: first linked account of type smart_wallet
- Entrypoint: smart-wallet routing and linking
- Attacker controls: linked_accounts contents including multiple smart wallets
- Exploit idea: Present linked-account sets that flip each branch.
- Invariant to test: Provisioning policy must be evaluated against server-confirmed account state.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: enumerate account sets through getUserSmartWallet: first linked account of type smart_wallet and assert branch correctness.
