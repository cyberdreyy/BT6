# Q0916: unenroll requires only the current session in RecoveryOAuthApi.ts

## Question
unenrollMfa is gated by MFA but not by re-authentication; can an attacker with a live but unattended session use RecoveryOAuthApi.generateURL to remove the victim's second factor and then perform signing?

## Target
- File/function: [src/client/recovery/RecoveryOAuthApi.ts](src/client/recovery/RecoveryOAuthApi.ts) - RecoveryOAuthApi.generateURL, authorize (shares privy:state_code / privy:code_verifier with login OAuth)
- Entrypoint: privy.recovery.auth.generateURL(redirectTo) then authorize(code, state)
- Attacker controls: redirect_to, returned code/state, interleaving with privy.auth.oauth flows
- Exploit idea: Run unenroll on a warm session and follow with a signing operation.
- Invariant to test: Removing a second factor must require a fresh, explicit user authentication.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: run RecoveryOAuthApi.generateURL then a signature and assert the signature still demands MFA.
