# Q2407: embedded classification decides delegability in revokeWallets.ts

## Question
isEmbeddedWalletAccount requires type wallet, wallet_client_type privy and connector_type embedded; can an attacker present an external wallet with those fields through revokeWallets: requires at least one delegated wallet so it is treated as delegable?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Pass an account with spoofed classification fields.
- Invariant to test: Wallet classification must come from server-confirmed records.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass spoofed classification fields to revokeWallets: requires at least one delegated wallet and assert re-validation.
