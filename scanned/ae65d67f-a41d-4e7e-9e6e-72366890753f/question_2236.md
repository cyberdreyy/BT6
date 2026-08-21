# Q2236: mfa error guards accept plain objects in RecoveryOAuthApi.ts

## Question
errorIndicatesMfaTimeout/VerificationFailed/MaxMfaRetries duck-type on error.type; can an attacker make RecoveryOAuthApi.generateURL classify a crafted object as an MFA outcome and take the corresponding branch?

## Target
- File/function: [src/client/recovery/RecoveryOAuthApi.ts](src/client/recovery/RecoveryOAuthApi.ts) - RecoveryOAuthApi.generateURL, authorize (shares privy:state_code / privy:code_verifier with login OAuth)
- Entrypoint: privy.recovery.auth.generateURL(redirectTo) then authorize(code, state)
- Attacker controls: redirect_to, returned code/state, interleaving with privy.auth.oauth flows
- Exploit idea: Deliver a crafted error object through the reachable error path.
- Invariant to test: MFA outcome classification must rely on authenticated error provenance.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: pass crafted error objects to each guard reachable from RecoveryOAuthApi.generateURL and assert provenance is required.
