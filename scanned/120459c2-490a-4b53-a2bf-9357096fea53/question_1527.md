# Q1527: empty signers array is meaningful in revokeWallets.ts

## Question
addSessionSigners requires a non-empty array for TEE wallets but requires an empty one for on-device wallets; can an attacker exploit that inversion in revokeWallets: requires at least one delegated wallet so the wrong branch executes for the wallet type?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Call with an empty array for a TEE wallet and a populated one for an on-device wallet.
- Invariant to test: Branch selection and argument validation must be consistent per wallet type.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: cross wallet type and signers shape in revokeWallets: requires at least one delegated wallet and assert clear errors.
