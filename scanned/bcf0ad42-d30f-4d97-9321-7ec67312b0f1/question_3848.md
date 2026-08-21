# Q3848: smart wallet selection ignores deployment state in shouldCreateEmbeddedSolWallet.ts

## Question
getUserSmartWallet returns the account regardless of deployment status; can an attacker cause an undeployed smart wallet to be selected via shouldCreateEmbeddedSolWallet(user so a signature is produced that cannot be verified on chain?

## Target
- File/function: [src/utils/shouldCreateEmbeddedSolWallet.ts](src/utils/shouldCreateEmbeddedSolWallet.ts) - shouldCreateEmbeddedSolWallet(user, createOnLogin)
- Entrypoint: maybeCreateWalletOnLogin after every login
- Attacker controls: linked solana accounts and the createOnLogin setting
- Exploit idea: Select an undeployed smart wallet and sign.
- Invariant to test: Smart-wallet selection must consider deployment state.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: assert shouldCreateEmbeddedSolWallet(user exposes deployment state to callers.
