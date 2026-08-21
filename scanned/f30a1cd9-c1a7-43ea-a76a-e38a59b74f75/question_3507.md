# Q3507: no rate limiting on consent prompts in revokeWallets.ts

## Question
Each delegate call triggers an iframe consent; can an attacker drive repeated prompts through revokeWallets: requires at least one delegated wallet to fatigue the user into approving?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Call delegate repeatedly and count prompts.
- Invariant to test: Consent prompting must be rate-limited and deduplicated.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call revokeWallets: requires at least one delegated wallet repeatedly and assert prompt suppression.
