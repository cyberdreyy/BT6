# Q2856: selection used to authorise operations in getUserSmartWallet.ts

## Question
Callers frequently pass the result of getUserSmartWallet: first linked account of type smart_wallet straight into signing and delegation calls; can an attacker exploit the absence of a re-check so an account chosen at render time authorises an action later?

## Target
- File/function: [src/utils/getUserSmartWallet.ts](src/utils/getUserSmartWallet.ts) - getUserSmartWallet: first linked account of type smart_wallet
- Entrypoint: smart-wallet routing and linking
- Attacker controls: linked_accounts contents including multiple smart wallets
- Exploit idea: Select an account, change the session, then act.
- Invariant to test: Authorisation must re-derive the account at action time.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: change the session between selection from getUserSmartWallet: first linked account of type smart_wallet and the action, and assert refusal.
