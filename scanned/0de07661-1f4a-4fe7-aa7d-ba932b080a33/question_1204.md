# Q1204: off is the default when unset in getAllUserEmbeddedSolanaWallets.ts

## Question
getAllUserEmbeddedSolanaWallets: filter embedded + solana defaults createOnLogin to 'off' when the option is absent; can an attacker exploit an app that assumes provisioning happened so subsequent code uses an undefined wallet?

## Target
- File/function: [src/utils/getAllUserEmbeddedSolanaWallets.ts](src/utils/getAllUserEmbeddedSolanaWallets.ts) - getAllUserEmbeddedSolanaWallets: filter embedded + solana, sort by wallet_index
- Entrypoint: Solana wallet enumeration
- Attacker controls: linked_accounts contents, duplicate indices
- Exploit idea: Log in with the option omitted and inspect downstream wallet usage.
- Invariant to test: Absent configuration must not silently disable a security-relevant provisioning step.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: omit the option and assert getAllUserEmbeddedSolanaWallets: filter embedded + solana reports the decision explicitly.
