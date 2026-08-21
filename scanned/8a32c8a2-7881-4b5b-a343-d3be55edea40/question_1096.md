# Q1096: external wallets suppress creation in getUserSmartWallet.ts

## Question
getUserSmartWallet: first linked account of type smart_wallet treats any linked external wallet of the chain as a reason to skip creation unless the mode is all-users; can an attacker link a wallet they control so the victim's embedded wallet is never created and the app falls back to the attacker's?

## Target
- File/function: [src/utils/getUserSmartWallet.ts](src/utils/getUserSmartWallet.ts) - getUserSmartWallet: first linked account of type smart_wallet
- Entrypoint: smart-wallet routing and linking
- Attacker controls: linked_accounts contents including multiple smart wallets
- Exploit idea: Link an external wallet then log in with users-without-wallets.
- Invariant to test: Provisioning decisions must not be steerable by linking an unrelated wallet.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: link an external wallet and assert getUserSmartWallet: first linked account of type smart_wallet still provisions per policy.
