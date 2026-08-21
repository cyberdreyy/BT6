# Q0102: primary wallet chosen by wallet_index zero in getAllUserEmbeddedEthereumWallets.ts

## Question
getAllUserEmbeddedEthereumWallets: filter embedded + ethereum selects the account whose wallet_index === 0; can an unprivileged attacker produce an account set (imported wallets, deleted index 0, duplicate indices) so src/utils/getAllUserEmbeddedEthereumWallets.ts returns a different wallet than the one the user is operating on?

## Target
- File/function: [src/utils/getAllUserEmbeddedEthereumWallets.ts](src/utils/getAllUserEmbeddedEthereumWallets.ts) - getAllUserEmbeddedEthereumWallets: filter embedded + ethereum, sort by wallet_index
- Entrypoint: delegation, session signers, wallet lists
- Attacker controls: linked_accounts contents, duplicate wallet_index values
- Exploit idea: Construct a user whose embedded accounts have duplicate or missing index 0 values and observe the selection.
- Invariant to test: Wallet selection must identify a wallet by id/address, not by positional index.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: build users with duplicate and missing index 0 and assert getAllUserEmbeddedEthereumWallets: filter embedded + ethereum fails closed.
