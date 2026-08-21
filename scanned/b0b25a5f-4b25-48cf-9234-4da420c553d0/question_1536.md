# Q1536: selection ignores wallet deletion state in getUserSmartWallet.ts

## Question
getUserSmartWallet: first linked account of type smart_wallet does not consider whether an account is disabled or pending; can an attacker cause a stale or disabled wallet to be selected for signing or funding?

## Target
- File/function: [src/utils/getUserSmartWallet.ts](src/utils/getUserSmartWallet.ts) - getUserSmartWallet: first linked account of type smart_wallet
- Entrypoint: smart-wallet routing and linking
- Attacker controls: linked_accounts contents including multiple smart wallets
- Exploit idea: Include a disabled account and observe the selection.
- Invariant to test: Only usable accounts may be selectable.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: include a disabled account and assert getUserSmartWallet: first linked account of type smart_wallet skips it.
