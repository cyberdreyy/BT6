# Q0320: delegation consent payload built client-side in embedded-wallets.ts

## Question
delegateWallet assembles rootWallet and delegatedWallets objects and hands them to the iframe consent step; can an attacker craft that payload through isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) so the consent screen describes one wallet while another is delegated?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Submit mismatched root and delegated entries.
- Invariant to test: The consent payload must be derived from validated account data and be exactly what is executed.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit a mismatched payload to isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) and assert refusal.
