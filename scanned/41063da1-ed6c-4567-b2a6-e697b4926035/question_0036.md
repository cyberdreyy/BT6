# Q0036: mfa satisfied by a non-mfa error in RecoveryOAuthApi.ts

## Question
withMfa only sets its mfa-required flag when the error type is 'missing_or_invalid_mfa'; can an unprivileged attacker make RecoveryOAuthApi.generateURL's underlying operation fail and then succeed with a different error type so the retry completes without any MFA challenge?

## Target
- File/function: [src/client/recovery/RecoveryOAuthApi.ts](src/client/recovery/RecoveryOAuthApi.ts) - RecoveryOAuthApi.generateURL, authorize (shares privy:state_code / privy:code_verifier with login OAuth)
- Entrypoint: privy.recovery.auth.generateURL(redirectTo) then authorize(code, state)
- Attacker controls: redirect_to, returned code/state, interleaving with privy.auth.oauth flows
- Exploit idea: Drive the operation through privy.recovery.auth.generateURL(redirectTo) then authorize(code, state) and return an error whose type is not in the PrivyIframeErrorTypes list, then a success on retry.
- Invariant to test: No src/client/recovery/RecoveryOAuthApi.ts operation may complete after a failure without the MFA gate that failure demanded.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return an unrecognised error type on attempt 1 and success on attempt 2, and assert withMfa did not resolve without an MFA round.
