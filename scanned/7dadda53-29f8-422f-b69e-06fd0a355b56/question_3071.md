# Q3071: multiple embedded wallets confuse the app in getUserEmbeddedEthereumWallet.ts

## Question
getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 exposes lists that callers index into; can an attacker add a wallet so index-based access in the app selects a different wallet than before?

## Target
- File/function: [src/utils/getUserEmbeddedEthereumWallet.ts](src/utils/getUserEmbeddedEthereumWallet.ts) - getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0
- Entrypoint: entropy resolution, root-wallet selection, create-on-login checks
- Attacker controls: the user object's linked_accounts array contents and ordering
- Exploit idea: Add a wallet and compare index-based selections before and after.
- Invariant to test: Wallet references must be stable identifiers, not list indices.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 exposes stable identifiers for each wallet.
