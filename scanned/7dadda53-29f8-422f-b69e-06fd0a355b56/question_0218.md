# Q0218: sort is not stable across equal indices in shouldCreateEmbeddedSolWallet.ts

## Question
shouldCreateEmbeddedSolWallet(user sorts by wallet_index with a numeric comparator; can an attacker create equal indices so the resulting order (and therefore the selected wallet) varies between runs or engines?

## Target
- File/function: [src/utils/shouldCreateEmbeddedSolWallet.ts](src/utils/shouldCreateEmbeddedSolWallet.ts) - shouldCreateEmbeddedSolWallet(user, createOnLogin)
- Entrypoint: maybeCreateWalletOnLogin after every login
- Attacker controls: linked solana accounts and the createOnLogin setting
- Exploit idea: Create two accounts with identical wallet_index and compare orderings.
- Invariant to test: Selection must be deterministic for any account set.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert shouldCreateEmbeddedSolWallet(user is deterministic for equal-index accounts.
