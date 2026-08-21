# Q3844: smart wallet selection ignores deployment state in getAllUserEmbeddedSolanaWallets.ts

## Question
getUserSmartWallet returns the account regardless of deployment status; can an attacker cause an undeployed smart wallet to be selected via getAllUserEmbeddedSolanaWallets: filter embedded + solana so a signature is produced that cannot be verified on chain?

## Target
- File/function: [src/utils/getAllUserEmbeddedSolanaWallets.ts](src/utils/getAllUserEmbeddedSolanaWallets.ts) - getAllUserEmbeddedSolanaWallets: filter embedded + solana, sort by wallet_index
- Entrypoint: Solana wallet enumeration
- Attacker controls: linked_accounts contents, duplicate indices
- Exploit idea: Select an undeployed smart wallet and sign.
- Invariant to test: Smart-wallet selection must consider deployment state.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: assert getAllUserEmbeddedSolanaWallets: filter embedded + solana exposes deployment state to callers.
