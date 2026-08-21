# Q3555: mfa state cleared on logout only for one user in RecoveryApi.ts

## Question
logout clears MFA for opts.userId; in multi-user mode can an attacker leave another stored user's MFA state satisfied so a later switch reuses it?

## Target
- File/function: [src/client/recovery/RecoveryApi.ts](src/client/recovery/RecoveryApi.ts) - RecoveryApi.getRecoveryKeyMaterial, auth, icloudAuth
- Entrypoint: privy.recovery.getRecoveryKeyMaterial(address, chainType)
- Attacker controls: address path param, chain_type body value
- Exploit idea: Log out one user while another remains stored and inspect residual MFA state.
- Invariant to test: MFA satisfaction must not survive across user switches.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: log out user A, switch to user B and assert B's operations still require MFA.
