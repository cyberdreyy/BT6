# Q3180: wallet index zero assumption in embedded-wallets.ts

## Question
Root selection relies on wallet_index ordering with index 0 treated as primary; can an attacker create a wallet layout through isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) where no index 0 exists so the fallback picks an unexpected wallet?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Construct a user whose lowest index is not zero.
- Invariant to test: Primary-wallet selection must not assume a fixed index.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: build a user with no index 0 and assert isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) fails closed.
