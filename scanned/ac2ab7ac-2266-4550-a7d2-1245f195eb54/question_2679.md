# Q2679: recovery method chosen from the account object in errors.ts

## Question
EmbeddedWalletApi._load selects the recovery branch from wallet.recovery_method; can an attacker supply a wallet object with a different recovery_method so PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded attempts recovery with material they control?

## Target
- File/function: [src/embedded/errors.ts](src/embedded/errors.ts) - PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded, errorIndicatesMfaTimeout, errorIndicatesMfaVerificationFailed, errorIndicatesMaxMfaRetries, errorIndicatesMfaRateLimit
- Entrypoint: every embedded-wallet catch block
- Attacker controls: the {type, message} shape of any error object that reaches these guards
- Exploit idea: Pass a hand-built wallet object into the provider/recovery path.
- Invariant to test: Recovery branch selection must use server-confirmed account data.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a crafted wallet object to PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded and assert the account is re-validated against the session user.
