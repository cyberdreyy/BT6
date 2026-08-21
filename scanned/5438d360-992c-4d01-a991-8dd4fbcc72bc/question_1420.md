# Q1420: signers array unvalidated in embedded-wallets.ts

## Question
addSessionSigners concatenates the caller's signers onto the existing list; can an attacker add a signer key they control through isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) so future server-side signing is possible without the user?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Pass an attacker signer entry and inspect the resulting wallet record.
- Invariant to test: Every added signer must be user-approved and validated.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass an arbitrary signer to isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) and assert an approval gate.
