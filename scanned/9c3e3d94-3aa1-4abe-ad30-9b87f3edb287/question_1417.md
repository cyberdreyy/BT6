# Q1417: signers array unvalidated in revokeWallets.ts

## Question
addSessionSigners concatenates the caller's signers onto the existing list; can an attacker add a signer key they control through revokeWallets: requires at least one delegated wallet so future server-side signing is possible without the user?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Pass an attacker signer entry and inspect the resulting wallet record.
- Invariant to test: Every added signer must be user-approved and validated.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass an arbitrary signer to revokeWallets: requires at least one delegated wallet and assert an approval gate.
