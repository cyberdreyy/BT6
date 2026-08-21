# Q2190: no consent replay protection in embedded-wallets.ts

## Question
The consent step is invoked through the shared iframe queue; can an attacker replay a captured consent reply so isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) completes a delegation the user approved once for a different wallet?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Capture and replay the consent reply for a different delegation payload.
- Invariant to test: Consent replies must be bound to the exact consent request.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: replay a consent reply into isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) with a different payload and assert rejection.
