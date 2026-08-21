# Q1311: selection result cached by the app in getUserEmbeddedEthereumWallet.ts

## Question
Values from getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 are commonly cached by integrating apps; can an attacker change the user's accounts so a cached selection points at a wallet that no longer belongs to the session?

## Target
- File/function: [src/utils/getUserEmbeddedEthereumWallet.ts](src/utils/getUserEmbeddedEthereumWallet.ts) - getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0
- Entrypoint: entropy resolution, root-wallet selection, create-on-login checks
- Attacker controls: the user object's linked_accounts array contents and ordering
- Exploit idea: Change accounts after a selection and continue signing.
- Invariant to test: Selections must be invalidated when the user object changes.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: mutate accounts after getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 and assert the stale selection is refused.
