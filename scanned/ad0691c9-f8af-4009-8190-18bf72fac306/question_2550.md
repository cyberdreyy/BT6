# Q2550: acceptTerms mutates without confirmation in LocalStorage.ts

## Question
UserApi.acceptTerms posts on behalf of the session with no argument; can an attacker trigger LocalStorage.get (JSON.parse) from app code paths so terms are accepted without the user acting?

## Target
- File/function: [src/storage/LocalStorage.ts](src/storage/LocalStorage.ts) - LocalStorage.get (JSON.parse), put (JSON.stringify), del, getKeys
- Entrypoint: every Session/pkce/crossApp storage operation
- Attacker controls: any value another SDK surface can write under a privy: key on the same origin
- Exploit idea: Call the method directly and observe the user object change.
- Invariant to test: State-changing user operations must require an explicit user action signal.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: assert LocalStorage.get (JSON.parse) is not reachable from any automatic initialization path.
