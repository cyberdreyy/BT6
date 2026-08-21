# Q0541: chain type filter is a string compare in getUserEmbeddedEthereumWallet.ts

## Question
getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 filters on chain_type equality; can an attacker supply an account with an unexpected chain_type casing or alias so it is included or excluded incorrectly?

## Target
- File/function: [src/utils/getUserEmbeddedEthereumWallet.ts](src/utils/getUserEmbeddedEthereumWallet.ts) - getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0
- Entrypoint: entropy resolution, root-wallet selection, create-on-login checks
- Attacker controls: the user object's linked_accounts array contents and ordering
- Exploit idea: Pass chain_type variants such as 'Ethereum' or 'ethereum '.
- Invariant to test: Chain type matching must be canonical.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: table-test chain_type variants through getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0.
