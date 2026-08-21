# Q1532: selection ignores wallet deletion state in getAllUserEmbeddedEthereumWallets.ts

## Question
getAllUserEmbeddedEthereumWallets: filter embedded + ethereum does not consider whether an account is disabled or pending; can an attacker cause a stale or disabled wallet to be selected for signing or funding?

## Target
- File/function: [src/utils/getAllUserEmbeddedEthereumWallets.ts](src/utils/getAllUserEmbeddedEthereumWallets.ts) - getAllUserEmbeddedEthereumWallets: filter embedded + ethereum, sort by wallet_index
- Entrypoint: delegation, session signers, wallet lists
- Attacker controls: linked_accounts contents, duplicate wallet_index values
- Exploit idea: Include a disabled account and observe the selection.
- Invariant to test: Only usable accounts may be selectable.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: include a disabled account and assert getAllUserEmbeddedEthereumWallets: filter embedded + ethereum skips it.
