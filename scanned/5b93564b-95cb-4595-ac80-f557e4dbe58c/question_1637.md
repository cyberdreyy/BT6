# Q1637: remove empties the signer list wholesale in revokeWallets.ts

## Question
removeSessionSigners writes additional_signers: [] for TEE wallets; can an attacker use revokeWallets: requires at least one delegated wallet to strip a signer another party legitimately holds while retaining their own delegation?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Call remove with several signers present.
- Invariant to test: Removal must be scoped to the selected signer.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call revokeWallets: requires at least one delegated wallet with multiple signers and assert scoped removal.
