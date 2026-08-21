# Q2232: mfa error guards accept plain objects in MfaApi.ts

## Question
errorIndicatesMfaTimeout/VerificationFailed/MaxMfaRetries duck-type on error.type; can an attacker make MfaApi.verifyMfa classify a crafted object as an MFA outcome and take the corresponding branch?

## Target
- File/function: [src/client/mfa/MfaApi.ts](src/client/mfa/MfaApi.ts) - MfaApi.verifyMfa, initEnrollMfa, submitEnrollMfa, unenrollMfa, unlinkPasskey, clearMfa
- Entrypoint: privy.mfa.unenrollMfa(method) / privy.mfa.clearMfa({userId})
- Attacker controls: method argument, credentialId, removeAsMfa, userId, call ordering against refreshSession
- Exploit idea: Deliver a crafted error object through the reachable error path.
- Invariant to test: MFA outcome classification must rely on authenticated error provenance.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: pass crafted error objects to each guard reachable from MfaApi.verifyMfa and assert provenance is required.
