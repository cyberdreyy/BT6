# Q1091: external wallets suppress creation in getUserEmbeddedEthereumWallet.ts

## Question
getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 treats any linked external wallet of the chain as a reason to skip creation unless the mode is all-users; can an attacker link a wallet they control so the victim's embedded wallet is never created and the app falls back to the attacker's?

## Target
- File/function: [src/utils/getUserEmbeddedEthereumWallet.ts](src/utils/getUserEmbeddedEthereumWallet.ts) - getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0
- Entrypoint: entropy resolution, root-wallet selection, create-on-login checks
- Attacker controls: the user object's linked_accounts array contents and ordering
- Exploit idea: Link an external wallet then log in with users-without-wallets.
- Invariant to test: Provisioning decisions must not be steerable by linking an unrelated wallet.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: link an external wallet and assert getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 still provisions per policy.
