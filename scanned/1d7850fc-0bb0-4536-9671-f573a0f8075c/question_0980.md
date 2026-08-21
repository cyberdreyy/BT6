# Q0980: TEE wallets rejected only client-side in embedded-wallets.ts

## Question
delegateWallet and revokeWallets throw unsupported_wallet_type for unified (privy-v2) wallets based on the account object; can an attacker present an account through isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) that evades the check and reaches the delegation path?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Pass an account missing the id field or with a different recovery_method.
- Invariant to test: Custody-type checks must use server-confirmed account records.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass evasive account objects to isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) and assert re-validation.
