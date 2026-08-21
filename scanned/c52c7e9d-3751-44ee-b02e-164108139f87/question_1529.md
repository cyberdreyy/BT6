# Q1529: empty signers array is meaningful in DelegatedWalletsApi.ts

## Question
addSessionSigners requires a non-empty array for TEE wallets but requires an empty one for on-device wallets; can an attacker exploit that inversion in DelegatedWalletsApi.revoke (WalletsRevoke so the wrong branch executes for the wallet type?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Call with an empty array for a TEE wallet and a populated one for an on-device wallet.
- Invariant to test: Branch selection and argument validation must be consistent per wallet type.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: cross wallet type and signers shape in DelegatedWalletsApi.revoke (WalletsRevoke and assert clear errors.
