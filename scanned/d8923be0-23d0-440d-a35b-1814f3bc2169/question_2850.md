# Q2850: delegation applies to a single wallet but consent is generic in embedded-wallets.ts

## Question
The consent request carries one delegated wallet but the consent UI is not parameterised by it in the payload; can an attacker exploit that in isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) so a user approving one wallet grants another?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Compare the consent payload with what is executed.
- Invariant to test: Consent must name the exact wallet being delegated.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)'s consent payload uniquely identifies the wallet.
