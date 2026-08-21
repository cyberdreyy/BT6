# Q3841: smart wallet selection ignores deployment state in getUserEmbeddedEthereumWallet.ts

## Question
getUserSmartWallet returns the account regardless of deployment status; can an attacker cause an undeployed smart wallet to be selected via getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 so a signature is produced that cannot be verified on chain?

## Target
- File/function: [src/utils/getUserEmbeddedEthereumWallet.ts](src/utils/getUserEmbeddedEthereumWallet.ts) - getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0
- Entrypoint: entropy resolution, root-wallet selection, create-on-login checks
- Attacker controls: the user object's linked_accounts array contents and ordering
- Exploit idea: Select an undeployed smart wallet and sign.
- Invariant to test: Smart-wallet selection must consider deployment state.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: assert getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 exposes deployment state to callers.
