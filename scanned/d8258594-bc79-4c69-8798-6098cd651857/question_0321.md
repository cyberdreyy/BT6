# Q0321: null user returns an empty result in getUserEmbeddedEthereumWallet.ts

## Question
getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 returns null or [] for a null user; can an attacker exploit that silent empty result so a caller proceeds with an undefined wallet and signs or funds with the wrong account?

## Target
- File/function: [src/utils/getUserEmbeddedEthereumWallet.ts](src/utils/getUserEmbeddedEthereumWallet.ts) - getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0
- Entrypoint: entropy resolution, root-wallet selection, create-on-login checks
- Attacker controls: the user object's linked_accounts array contents and ordering
- Exploit idea: Call the selection with a null user during a session gap.
- Invariant to test: Absence of a user must be an explicit error for wallet-selecting callers.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 with null and assert callers cannot proceed.
