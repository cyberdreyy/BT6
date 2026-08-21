# Q0871: selection helpers feed entropy derivation in getUserEmbeddedEthereumWallet.ts

## Question
The values returned by getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 flow into entropy identity and provider construction; can an attacker influence the selection so signing occurs under a different key than the app displayed?

## Target
- File/function: [src/utils/getUserEmbeddedEthereumWallet.ts](src/utils/getUserEmbeddedEthereumWallet.ts) - getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0
- Entrypoint: entropy resolution, root-wallet selection, create-on-login checks
- Attacker controls: the user object's linked_accounts array contents and ordering
- Exploit idea: Trace the selected account into the entropy and provider path.
- Invariant to test: The displayed wallet and the signing wallet must be the same account.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: assert the account from getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 equals the account used in the signing request.
