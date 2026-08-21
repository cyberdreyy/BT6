# Q1200: wallet list built by concatenation in embedded-wallets.ts

## Question
getAllUserEmbeddedWallets concatenates ethereum then solana wallets; can an attacker exploit ordering assumptions in isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) so an index-based selection picks the wrong wallet?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Construct users where the concatenation order changes which wallet is first.
- Invariant to test: Wallet selection must be by identity, not by position.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: permute account order and assert isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) selects the same wallet.
