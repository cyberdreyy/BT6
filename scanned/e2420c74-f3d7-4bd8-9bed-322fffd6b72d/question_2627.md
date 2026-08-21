# Q2627: delegation requires only a live session in revokeWallets.ts

## Question
No MFA or re-authentication gates delegateWallet beyond the iframe consent; can an attacker with a warm session use revokeWallets: requires at least one delegated wallet to grant delegation and then sign without further checks?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Run delegate then a signing operation on a warm session.
- Invariant to test: Granting persistent signing authority must require a strong, explicit authorisation.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: run revokeWallets: requires at least one delegated wallet then sign and assert an MFA/re-auth gate applied.
