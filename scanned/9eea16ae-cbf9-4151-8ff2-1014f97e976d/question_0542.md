# Q0542: chain type filter is a string compare in getAllUserEmbeddedEthereumWallets.ts

## Question
getAllUserEmbeddedEthereumWallets: filter embedded + ethereum filters on chain_type equality; can an attacker supply an account with an unexpected chain_type casing or alias so it is included or excluded incorrectly?

## Target
- File/function: [src/utils/getAllUserEmbeddedEthereumWallets.ts](src/utils/getAllUserEmbeddedEthereumWallets.ts) - getAllUserEmbeddedEthereumWallets: filter embedded + ethereum, sort by wallet_index
- Entrypoint: delegation, session signers, wallet lists
- Attacker controls: linked_accounts contents, duplicate wallet_index values
- Exploit idea: Pass chain_type variants such as 'Ethereum' or 'ethereum '.
- Invariant to test: Chain type matching must be canonical.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: table-test chain_type variants through getAllUserEmbeddedEthereumWallets: filter embedded + ethereum.
