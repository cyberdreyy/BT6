# Q0977: TEE wallets rejected only client-side in revokeWallets.ts

## Question
delegateWallet and revokeWallets throw unsupported_wallet_type for unified (privy-v2) wallets based on the account object; can an attacker present an account through revokeWallets: requires at least one delegated wallet that evades the check and reaches the delegation path?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Pass an account missing the id field or with a different recovery_method.
- Invariant to test: Custody-type checks must use server-confirmed account records.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass evasive account objects to revokeWallets: requires at least one delegated wallet and assert re-validation.
