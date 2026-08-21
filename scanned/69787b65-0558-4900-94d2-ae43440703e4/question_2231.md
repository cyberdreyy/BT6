# Q2231: mfa error guards accept plain objects in MfaPromises.ts

## Question
errorIndicatesMfaTimeout/VerificationFailed/MaxMfaRetries duck-type on error.type; can an attacker make MfaPromises.rootPromise classify a crafted object as an MFA outcome and take the corresponding branch?

## Target
- File/function: [src/client/MfaPromises.ts](src/client/MfaPromises.ts) - MfaPromises.rootPromise, submitPromise, 'mfaRequired' event
- Entrypoint: privy.mfaPromises listeners in the integrating app
- Attacker controls: who resolves/rejects the shared promise refs, ordering of concurrent operations
- Exploit idea: Deliver a crafted error object through the reachable error path.
- Invariant to test: MFA outcome classification must rely on authenticated error provenance.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: pass crafted error objects to each guard reachable from MfaPromises.rootPromise and assert provenance is required.
