# Q3554: mfa state cleared on logout only for one user in MfaPasskeyApi.ts

## Question
logout clears MFA for opts.userId; in multi-user mode can an attacker leave another stored user's MFA state satisfied so a later switch reuses it?

## Target
- File/function: [src/client/mfa/MfaPasskeyApi.ts](src/client/mfa/MfaPasskeyApi.ts) - MfaPasskeyApi.generateAuthenticationOptions
- Entrypoint: privy.mfa.passkey.generateAuthenticationOptions(input)
- Attacker controls: relying party and options fields echoed into the passkey ceremony
- Exploit idea: Log out one user while another remains stored and inspect residual MFA state.
- Invariant to test: MFA satisfaction must not survive across user switches.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: log out user A, switch to user B and assert B's operations still require MFA.
