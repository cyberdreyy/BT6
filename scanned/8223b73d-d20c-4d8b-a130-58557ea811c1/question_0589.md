# Q0589: mfaRequired event carries no operation identity in errors.ts

## Question
The 'mfaRequired' event emitted from src/embedded/errors.ts does not identify which operation triggered it; can an attacker exploit this so the app collects a code for the wrong pending action?

## Target
- File/function: [src/embedded/errors.ts](src/embedded/errors.ts) - PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded, errorIndicatesMfaTimeout, errorIndicatesMfaVerificationFailed, errorIndicatesMaxMfaRetries, errorIndicatesMfaRateLimit
- Entrypoint: every embedded-wallet catch block
- Attacker controls: the {type, message} shape of any error object that reaches these guards
- Exploit idea: Trigger two operations and inspect the event payload the app receives.
- Invariant to test: MFA prompts must be attributable to the exact operation awaiting them.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: assert the event payload emitted during PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded identifies the pending operation.
