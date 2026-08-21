# Q1092: external wallets suppress creation in getAllUserEmbeddedEthereumWallets.ts

## Question
getAllUserEmbeddedEthereumWallets: filter embedded + ethereum treats any linked external wallet of the chain as a reason to skip creation unless the mode is all-users; can an attacker link a wallet they control so the victim's embedded wallet is never created and the app falls back to the attacker's?

## Target
- File/function: [src/utils/getAllUserEmbeddedEthereumWallets.ts](src/utils/getAllUserEmbeddedEthereumWallets.ts) - getAllUserEmbeddedEthereumWallets: filter embedded + ethereum, sort by wallet_index
- Entrypoint: delegation, session signers, wallet lists
- Attacker controls: linked_accounts contents, duplicate wallet_index values
- Exploit idea: Link an external wallet then log in with users-without-wallets.
- Invariant to test: Provisioning decisions must not be steerable by linking an unrelated wallet.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: link an external wallet and assert getAllUserEmbeddedEthereumWallets: filter embedded + ethereum still provisions per policy.
