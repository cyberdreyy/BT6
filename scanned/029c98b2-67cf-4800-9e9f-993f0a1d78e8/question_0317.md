# Q0317: delegation consent payload built client-side in revokeWallets.ts

## Question
delegateWallet assembles rootWallet and delegatedWallets objects and hands them to the iframe consent step; can an attacker craft that payload through revokeWallets: requires at least one delegated wallet so the consent screen describes one wallet while another is delegated?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Submit mismatched root and delegated entries.
- Invariant to test: The consent payload must be derived from validated account data and be exactly what is executed.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit a mismatched payload to revokeWallets: requires at least one delegated wallet and assert refusal.
