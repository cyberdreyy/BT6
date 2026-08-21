# Q1426: linked_accounts order is server supplied in getUserSmartWallet.ts

## Question
getUserSmartWallet: first linked account of type smart_wallet depends on the order of user.linked_accounts as returned by the API; can an attacker influence that order so a different wallet becomes primary?

## Target
- File/function: [src/utils/getUserSmartWallet.ts](src/utils/getUserSmartWallet.ts) - getUserSmartWallet: first linked account of type smart_wallet
- Entrypoint: smart-wallet routing and linking
- Attacker controls: linked_accounts contents including multiple smart wallets
- Exploit idea: Return the same accounts in a different order and compare selections.
- Invariant to test: Selection must be order-independent.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: permute the account list and assert getUserSmartWallet: first linked account of type smart_wallet returns the same wallet.
