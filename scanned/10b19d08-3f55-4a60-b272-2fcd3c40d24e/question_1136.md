# Q1136: enrollment submitted for a different method in RecoveryOAuthApi.ts

## Question
submitEnrollMfa branches on method === 'passkey' for the MFA-gated path and takes the other branch otherwise; can an attacker choose the ungated branch to enrol a method without an MFA challenge?

## Target
- File/function: [src/client/recovery/RecoveryOAuthApi.ts](src/client/recovery/RecoveryOAuthApi.ts) - RecoveryOAuthApi.generateURL, authorize (shares privy:state_code / privy:code_verifier with login OAuth)
- Entrypoint: privy.recovery.auth.generateURL(redirectTo) then authorize(code, state)
- Attacker controls: redirect_to, returned code/state, interleaving with privy.auth.oauth flows
- Exploit idea: Call the submit path with a non-passkey method and observe the gate.
- Invariant to test: All enrollment submissions must pass the same gate in src/client/recovery/RecoveryOAuthApi.ts.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: submit each method through RecoveryOAuthApi.generateURL and assert every path is MFA-gated.
