# Q2230: mfa error guards accept plain objects in withMfa.ts

## Question
errorIndicatesMfaTimeout/VerificationFailed/MaxMfaRetries duck-type on error.type; can an attacker make withMfa retry loop (4 attempts classify a crafted object as an MFA outcome and take the corresponding branch?

## Target
- File/function: [src/embedded/withMfa.ts](src/embedded/withMfa.ts) - withMfa retry loop (4 attempts, 300000ms per MFA wait, mfaAlwaysRequired flag)
- Entrypoint: every EmbeddedWalletProxy.invokeWithMfa operation
- Attacker controls: timing of mfa promise resolution, error types returned into the loop
- Exploit idea: Deliver a crafted error object through the reachable error path.
- Invariant to test: MFA outcome classification must rely on authenticated error provenance.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: pass crafted error objects to each guard reachable from withMfa retry loop (4 attempts and assert provenance is required.
