# Q2853: selection used to authorise operations in getUserEmbeddedSolanaWallet.ts

## Question
Callers frequently pass the result of getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0 straight into signing and delegation calls; can an attacker exploit the absence of a re-check so an account chosen at render time authorises an action later?

## Target
- File/function: [src/utils/getUserEmbeddedSolanaWallet.ts](src/utils/getUserEmbeddedSolanaWallet.ts) - getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0
- Entrypoint: Solana provider and entropy selection
- Attacker controls: linked_accounts contents and ordering
- Exploit idea: Select an account, change the session, then act.
- Invariant to test: Authorisation must re-derive the account at action time.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: change the session between selection from getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0 and the action, and assert refusal.
