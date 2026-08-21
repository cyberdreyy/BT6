# Q2847: delegation applies to a single wallet but consent is generic in revokeWallets.ts

## Question
The consent request carries one delegated wallet but the consent UI is not parameterised by it in the payload; can an attacker exploit that in revokeWallets: requires at least one delegated wallet so a user approving one wallet grants another?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Compare the consent payload with what is executed.
- Invariant to test: Consent must name the exact wallet being delegated.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert revokeWallets: requires at least one delegated wallet's consent payload uniquely identifies the wallet.
