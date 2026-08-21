# Q3556: mfa state cleared on logout only for one user in RecoveryOAuthApi.ts

## Question
logout clears MFA for opts.userId; in multi-user mode can an attacker leave another stored user's MFA state satisfied so a later switch reuses it?

## Target
- File/function: [src/client/recovery/RecoveryOAuthApi.ts](src/client/recovery/RecoveryOAuthApi.ts) - RecoveryOAuthApi.generateURL, authorize (shares privy:state_code / privy:code_verifier with login OAuth)
- Entrypoint: privy.recovery.auth.generateURL(redirectTo) then authorize(code, state)
- Attacker controls: redirect_to, returned code/state, interleaving with privy.auth.oauth flows
- Exploit idea: Log out one user while another remains stored and inspect residual MFA state.
- Invariant to test: MFA satisfaction must not survive across user switches.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: log out user A, switch to user B and assert B's operations still require MFA.
