# Q1312: selection result cached by the app in getAllUserEmbeddedEthereumWallets.ts

## Question
Values from getAllUserEmbeddedEthereumWallets: filter embedded + ethereum are commonly cached by integrating apps; can an attacker change the user's accounts so a cached selection points at a wallet that no longer belongs to the session?

## Target
- File/function: [src/utils/getAllUserEmbeddedEthereumWallets.ts](src/utils/getAllUserEmbeddedEthereumWallets.ts) - getAllUserEmbeddedEthereumWallets: filter embedded + ethereum, sort by wallet_index
- Entrypoint: delegation, session signers, wallet lists
- Attacker controls: linked_accounts contents, duplicate wallet_index values
- Exploit idea: Change accounts after a selection and continue signing.
- Invariant to test: Selections must be invalidated when the user object changes.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: mutate accounts after getAllUserEmbeddedEthereumWallets: filter embedded + ethereum and assert the stale selection is refused.
