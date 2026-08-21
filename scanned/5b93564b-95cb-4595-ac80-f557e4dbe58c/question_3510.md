# Q3510: no rate limiting on consent prompts in embedded-wallets.ts

## Question
Each delegate call triggers an iframe consent; can an attacker drive repeated prompts through isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) to fatigue the user into approving?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Call delegate repeatedly and count prompts.
- Invariant to test: Consent prompting must be rate-limited and deduplicated.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) repeatedly and assert prompt suppression.
