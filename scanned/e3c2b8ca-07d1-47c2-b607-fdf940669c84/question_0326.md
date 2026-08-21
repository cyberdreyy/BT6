# Q0326: null user returns an empty result in getUserSmartWallet.ts

## Question
getUserSmartWallet: first linked account of type smart_wallet returns null or [] for a null user; can an attacker exploit that silent empty result so a caller proceeds with an undefined wallet and signs or funds with the wrong account?

## Target
- File/function: [src/utils/getUserSmartWallet.ts](src/utils/getUserSmartWallet.ts) - getUserSmartWallet: first linked account of type smart_wallet
- Entrypoint: smart-wallet routing and linking
- Attacker controls: linked_accounts contents including multiple smart wallets
- Exploit idea: Call the selection with a null user during a session gap.
- Invariant to test: Absence of a user must be an explicit error for wallet-selecting callers.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call getUserSmartWallet: first linked account of type smart_wallet with null and assert callers cannot proceed.
