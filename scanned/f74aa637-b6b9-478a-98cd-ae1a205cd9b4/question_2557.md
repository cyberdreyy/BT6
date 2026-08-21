# Q2557: acceptTerms mutates without confirmation in Error.ts

## Question
UserApi.acceptTerms posts on behalf of the session with no argument; can an attacker trigger PrivyApiError from app code paths so terms are accepted without the user acting?

## Target
- File/function: [src/Error.ts](src/Error.ts) - PrivyApiError, PrivyClientError, MoonpayApiError, createErrorFormatter, errorIndicatesMfaCanceled
- Entrypoint: every catch path in the SDK
- Attacker controls: error.code / error.message strings returned by any reachable response
- Exploit idea: Call the method directly and observe the user object change.
- Invariant to test: State-changing user operations must require an explicit user action signal.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: assert PrivyApiError is not reachable from any automatic initialization path.
