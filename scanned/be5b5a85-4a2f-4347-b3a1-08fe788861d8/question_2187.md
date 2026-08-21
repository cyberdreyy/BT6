# Q2187: no consent replay protection in revokeWallets.ts

## Question
The consent step is invoked through the shared iframe queue; can an attacker replay a captured consent reply so revokeWallets: requires at least one delegated wallet completes a delegation the user approved once for a different wallet?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Capture and replay the consent reply for a different delegation payload.
- Invariant to test: Consent replies must be bound to the exact consent request.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: replay a consent reply into revokeWallets: requires at least one delegated wallet with a different payload and assert rejection.
