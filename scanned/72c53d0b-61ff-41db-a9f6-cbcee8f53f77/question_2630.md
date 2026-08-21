# Q2630: delegation requires only a live session in embedded-wallets.ts

## Question
No MFA or re-authentication gates delegateWallet beyond the iframe consent; can an attacker with a warm session use isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) to grant delegation and then sign without further checks?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Run delegate then a signing operation on a warm session.
- Invariant to test: Granting persistent signing authority must require a strong, explicit authorisation.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: run isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) then sign and assert an MFA/re-auth gate applied.
