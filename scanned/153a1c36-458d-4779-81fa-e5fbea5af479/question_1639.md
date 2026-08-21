# Q1639: remove empties the signer list wholesale in DelegatedWalletsApi.ts

## Question
removeSessionSigners writes additional_signers: [] for TEE wallets; can an attacker use DelegatedWalletsApi.revoke (WalletsRevoke to strip a signer another party legitimately holds while retaining their own delegation?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Call remove with several signers present.
- Invariant to test: Removal must be scoped to the selected signer.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call DelegatedWalletsApi.revoke (WalletsRevoke with multiple signers and assert scoped removal.
