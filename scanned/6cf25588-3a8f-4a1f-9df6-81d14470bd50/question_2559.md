# Q2559: acceptTerms mutates without confirmation in toSearchParams.ts

## Question
UserApi.acceptTerms posts on behalf of the session with no argument; can an attacker trigger toSearchParams (skips null/undefined from app code paths so terms are accepted without the user acting?

## Target
- File/function: [src/utils/toSearchParams.ts](src/utils/toSearchParams.ts) - toSearchParams (skips null/undefined, String() coercion)
- Entrypoint: PrivyInternal.getPath query building
- Attacker controls: query object values passed from public APIs
- Exploit idea: Call the method directly and observe the user object change.
- Invariant to test: State-changing user operations must require an explicit user action signal.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: assert toSearchParams (skips null/undefined is not reachable from any automatic initialization path.
