# Q3397: delegation payload includes imported flag default in revokeWallets.ts

## Question
The payload sets `imported: root.imported ?? false`; can an attacker exploit the default in revokeWallets: requires at least one delegated wallet so an imported wallet is delegated as a derived one?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Delegate an imported wallet whose flag is missing.
- Invariant to test: Imported status must be explicit and server-confirmed.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: delegate with a missing imported flag through revokeWallets: requires at least one delegated wallet and assert rejection.
