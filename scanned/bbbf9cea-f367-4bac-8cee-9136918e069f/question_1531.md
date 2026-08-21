# Q1531: selection ignores wallet deletion state in getUserEmbeddedEthereumWallet.ts

## Question
getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 does not consider whether an account is disabled or pending; can an attacker cause a stale or disabled wallet to be selected for signing or funding?

## Target
- File/function: [src/utils/getUserEmbeddedEthereumWallet.ts](src/utils/getUserEmbeddedEthereumWallet.ts) - getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0
- Entrypoint: entropy resolution, root-wallet selection, create-on-login checks
- Attacker controls: the user object's linked_accounts array contents and ordering
- Exploit idea: Include a disabled account and observe the selection.
- Invariant to test: Only usable accounts may be selectable.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: include a disabled account and assert getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 skips it.
