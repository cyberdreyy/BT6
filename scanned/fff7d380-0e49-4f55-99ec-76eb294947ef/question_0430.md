# Q0430: ownership check by address equality in embedded-wallets.ts

## Question
delegateWallet finds the target with `chain_type === n && address === t`; can an attacker submit a checksummed or padded address through isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) that fails or passes this check incorrectly?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Pass mixed-case and padded variants of an owned address.
- Invariant to test: Ownership comparison must be canonical.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: table-test address forms through isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded).
