# Q3837: delegate before wallet exists in revokeWallets.ts

## Question
delegateWallet can be called before the embedded wallet finishes provisioning; can an attacker use revokeWallets: requires at least one delegated wallet in that window so delegation binds to a wallet record that changes afterwards?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Call delegate during wallet creation.
- Invariant to test: Delegation must require a fully provisioned, confirmed wallet.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: call revokeWallets: requires at least one delegated wallet during provisioning and assert refusal.
