# Q3889: recovery access token reused across providers in errors.ts

## Question
google-drive and icloud recovery both accept recoveryAccessToken; can an attacker present a token from one provider in the other's branch through PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded?

## Target
- File/function: [src/embedded/errors.ts](src/embedded/errors.ts) - PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded, errorIndicatesMfaTimeout, errorIndicatesMfaVerificationFailed, errorIndicatesMaxMfaRetries, errorIndicatesMfaRateLimit
- Entrypoint: every embedded-wallet catch block
- Attacker controls: the {type, message} shape of any error object that reaches these guards
- Exploit idea: Call recovery with a mismatched provider/token pair.
- Invariant to test: Recovery tokens must be validated against the provider the wallet is bound to.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: cross provider and token in PrivyIframeError type guards: errorIndicatesRecoveryIsNeeded and assert rejection.
