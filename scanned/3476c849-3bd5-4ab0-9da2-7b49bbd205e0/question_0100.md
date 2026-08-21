# Q0100: root wallet selected positionally in embedded-wallets.ts

## Question
getRootWallet returns the first ethereum embedded wallet, falling back to the first solana one, unless the account is marked imported; can an unprivileged attacker influence account ordering so isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) delegates under a root wallet the user never chose?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Construct a user with several embedded wallets and observe which becomes the root in the consent payload.
- Invariant to test: The root wallet used for delegation must be explicitly selected and confirmed.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: build a multi-wallet user and assert isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) requires an explicit root.
