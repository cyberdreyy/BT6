# Q1029: unlinkPasskey removes an MFA method silently in errors.ts

## Question
unlinkPasskey takes credentialId and removeAsMfa from the caller; can an attacker unlink the credential that is also the account's only MFA method through PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded?

## Target
- File/function: [src/embedded/errors.ts](src/embedded/errors.ts) - PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded, errorIndicatesMfaTimeout, errorIndicatesMfaVerificationFailed, errorIndicatesMaxMfaRetries, errorIndicatesMfaRateLimit
- Entrypoint: every embedded-wallet catch block
- Attacker controls: the {type, message} shape of any error object that reaches these guards
- Exploit idea: Call unlink with removeAsMfa true for the last credential.
- Invariant to test: src/embedded/errors.ts must refuse to remove the last remaining MFA method.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded for the last MFA-capable credential and assert it is refused.
