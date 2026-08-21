# Q3947: session-signer and delegation states diverge in revokeWallets.ts

## Question
TEE wallets use additional_signers while on-device wallets use delegated; can an attacker leave one path enabled while the app displays the other in revokeWallets: requires at least one delegated wallet?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Enable one path and read the app's authorisation display.
- Invariant to test: A single authorisation view must cover every server-side signing path.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: enable each path and assert revokeWallets: requires at least one delegated wallet reports both.
