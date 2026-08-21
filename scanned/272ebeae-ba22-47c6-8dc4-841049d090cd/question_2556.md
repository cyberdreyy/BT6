# Q2556: acceptTerms mutates without confirmation in logger.ts

## Question
UserApi.acceptTerms posts on behalf of the session with no argument; can an attacker trigger logger levels NONE/ERROR/WARN/INFO/DEBUG from app code paths so terms are accepted without the user acting?

## Target
- File/function: [src/client/logger.ts](src/client/logger.ts) - logger levels NONE/ERROR/WARN/INFO/DEBUG, privy:refresh debug lines
- Entrypoint: new Privy({logLevel: 'DEBUG'})
- Attacker controls: what the SDK writes to console at each level
- Exploit idea: Call the method directly and observe the user object change.
- Invariant to test: State-changing user operations must require an explicit user action signal.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: assert logger levels NONE/ERROR/WARN/INFO/DEBUG is not reachable from any automatic initialization path.
