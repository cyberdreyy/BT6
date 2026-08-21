# Q2892: verifyMfa reachable without a pending operation in MfaApi.ts

## Question
MfaApi.verifyMfa can be invoked directly; can an attacker call MfaApi.verifyMfa to consume an MFA code outside any operation, leaving a satisfied MFA state that a later operation reuses?

## Target
- File/function: [src/client/mfa/MfaApi.ts](src/client/mfa/MfaApi.ts) - MfaApi.verifyMfa, initEnrollMfa, submitEnrollMfa, unenrollMfa, unlinkPasskey, clearMfa
- Entrypoint: privy.mfa.unenrollMfa(method) / privy.mfa.clearMfa({userId})
- Attacker controls: method argument, credentialId, removeAsMfa, userId, call ordering against refreshSession
- Exploit idea: Call verifyMfa alone, then immediately start a signing operation.
- Invariant to test: An MFA verification must be consumed by the operation that required it.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: call MfaApi.verifyMfa then a signature and assert the signature still requires its own MFA round.
