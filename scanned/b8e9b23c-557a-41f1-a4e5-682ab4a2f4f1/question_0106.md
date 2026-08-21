# Q0106: primary wallet chosen by wallet_index zero in getUserSmartWallet.ts

## Question
getUserSmartWallet: first linked account of type smart_wallet selects the account whose wallet_index === 0; can an unprivileged attacker produce an account set (imported wallets, deleted index 0, duplicate indices) so src/utils/getUserSmartWallet.ts returns a different wallet than the one the user is operating on?

## Target
- File/function: [src/utils/getUserSmartWallet.ts](src/utils/getUserSmartWallet.ts) - getUserSmartWallet: first linked account of type smart_wallet
- Entrypoint: smart-wallet routing and linking
- Attacker controls: linked_accounts contents including multiple smart wallets
- Exploit idea: Construct a user whose embedded accounts have duplicate or missing index 0 values and observe the selection.
- Invariant to test: Wallet selection must identify a wallet by id/address, not by positional index.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: build users with duplicate and missing index 0 and assert getUserSmartWallet: first linked account of type smart_wallet fails closed.
