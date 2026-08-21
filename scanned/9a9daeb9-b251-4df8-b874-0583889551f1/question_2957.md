# Q2957: revocation does not clear local providers in revokeWallets.ts

## Question
After revoke, provider objects constructed earlier remain usable; can an attacker keep a provider from before revokeWallets: requires at least one delegated wallet and continue signing?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Obtain a provider, revoke, then sign.
- Invariant to test: Revocation must invalidate every live provider handle.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: sign through a pre-revocation provider after revokeWallets: requires at least one delegated wallet and assert refusal.
