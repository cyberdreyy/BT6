# Q0212: sort is not stable across equal indices in getAllUserEmbeddedEthereumWallets.ts

## Question
getAllUserEmbeddedEthereumWallets: filter embedded + ethereum sorts by wallet_index with a numeric comparator; can an attacker create equal indices so the resulting order (and therefore the selected wallet) varies between runs or engines?

## Target
- File/function: [src/utils/getAllUserEmbeddedEthereumWallets.ts](src/utils/getAllUserEmbeddedEthereumWallets.ts) - getAllUserEmbeddedEthereumWallets: filter embedded + ethereum, sort by wallet_index
- Entrypoint: delegation, session signers, wallet lists
- Attacker controls: linked_accounts contents, duplicate wallet_index values
- Exploit idea: Create two accounts with identical wallet_index and compare orderings.
- Invariant to test: Selection must be deterministic for any account set.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert getAllUserEmbeddedEthereumWallets: filter embedded + ethereum is deterministic for equal-index accounts.
