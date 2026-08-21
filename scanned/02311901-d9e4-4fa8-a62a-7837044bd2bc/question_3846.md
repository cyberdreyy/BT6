# Q3846: smart wallet selection ignores deployment state in getUserSmartWallet.ts

## Question
getUserSmartWallet returns the account regardless of deployment status; can an attacker cause an undeployed smart wallet to be selected via getUserSmartWallet: first linked account of type smart_wallet so a signature is produced that cannot be verified on chain?

## Target
- File/function: [src/utils/getUserSmartWallet.ts](src/utils/getUserSmartWallet.ts) - getUserSmartWallet: first linked account of type smart_wallet
- Entrypoint: smart-wallet routing and linking
- Attacker controls: linked_accounts contents including multiple smart wallets
- Exploit idea: Select an undeployed smart wallet and sign.
- Invariant to test: Smart-wallet selection must consider deployment state.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: assert getUserSmartWallet: first linked account of type smart_wallet exposes deployment state to callers.
