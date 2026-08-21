# Q3843: smart wallet selection ignores deployment state in getUserEmbeddedSolanaWallet.ts

## Question
getUserSmartWallet returns the account regardless of deployment status; can an attacker cause an undeployed smart wallet to be selected via getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0 so a signature is produced that cannot be verified on chain?

## Target
- File/function: [src/utils/getUserEmbeddedSolanaWallet.ts](src/utils/getUserEmbeddedSolanaWallet.ts) - getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0
- Entrypoint: Solana provider and entropy selection
- Attacker controls: linked_accounts contents and ordering
- Exploit idea: Select an undeployed smart wallet and sign.
- Invariant to test: Smart-wallet selection must consider deployment state.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: assert getUserEmbeddedSolanaWallet: first solana embedded account with wallet_index === 0 exposes deployment state to callers.
