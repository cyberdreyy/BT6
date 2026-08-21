# Q0427: ownership check by address equality in revokeWallets.ts

## Question
delegateWallet finds the target with `chain_type === n && address === t`; can an attacker submit a checksummed or padded address through revokeWallets: requires at least one delegated wallet that fails or passes this check incorrectly?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Pass mixed-case and padded variants of an owned address.
- Invariant to test: Ownership comparison must be canonical.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: table-test address forms through revokeWallets: requires at least one delegated wallet.
