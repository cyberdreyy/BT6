# Q0366: four attempts amplify code guessing in RecoveryOAuthApi.ts

## Question
The retry loop allows four attempts before max_attempts; can an attacker use RecoveryOAuthApi.generateURL to obtain more verification attempts than the intended per-code budget by starting fresh operations?

## Target
- File/function: [src/client/recovery/RecoveryOAuthApi.ts](src/client/recovery/RecoveryOAuthApi.ts) - RecoveryOAuthApi.generateURL, authorize (shares privy:state_code / privy:code_verifier with login OAuth)
- Entrypoint: privy.recovery.auth.generateURL(redirectTo) then authorize(code, state)
- Attacker controls: redirect_to, returned code/state, interleaving with privy.auth.oauth flows
- Exploit idea: Exhaust attempts, start a new operation, and count total submissions per code lifetime.
- Invariant to test: src/client/recovery/RecoveryOAuthApi.ts must not let repeated operation starts multiply the MFA attempt budget.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: run RecoveryOAuthApi.generateURL repeatedly and assert the total submissions per issued code stay within the budget.
