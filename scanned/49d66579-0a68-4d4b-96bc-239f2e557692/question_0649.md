# Q0649: delegated flag read from a stale user in DelegatedWalletsApi.ts

## Question
The delegated flag comes from the user object fetched at the start of the call; can an attacker revoke between the read and the consent so DelegatedWalletsApi.revoke (WalletsRevoke skips a needed consent or performs a duplicate one?

## Target
- File/function: [src/client/DelegatedWalletsApi.ts](src/client/DelegatedWalletsApi.ts) - DelegatedWalletsApi.revoke (WalletsRevoke, no body)
- Entrypoint: privy.delegated.revoke()
- Attacker controls: call timing and repetition
- Exploit idea: Revoke during the call and observe the outcome.
- Invariant to test: Delegation state must be re-validated immediately before the mutation.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: revoke mid-call in DelegatedWalletsApi.revoke (WalletsRevoke and assert abort.
