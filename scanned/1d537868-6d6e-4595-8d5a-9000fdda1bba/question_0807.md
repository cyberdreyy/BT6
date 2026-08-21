# Q0807: clearMfa userId is caller supplied in RecoveryICloudApi.ts

## Question
clearMfa forwards the caller's userId to the iframe; can an attacker pass another user's id through RecoveryICloudApi.init to drop MFA state that is not theirs?

## Target
- File/function: [src/client/recovery/RecoveryICloudApi.ts](src/client/recovery/RecoveryICloudApi.ts) - RecoveryICloudApi.init, getICloudConfiguration
- Entrypoint: privy.recovery.icloudAuth.init(clientType)
- Attacker controls: client_type value, response fields used as recovery configuration
- Exploit idea: Call the clear path with a foreign user id.
- Invariant to test: MFA clearing must be scoped to the authenticated session's own user.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call RecoveryICloudApi.init with a foreign userId and assert the session's own id is used or the call is refused.
