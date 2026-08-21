# Q2553: acceptTerms mutates without confirmation in Privy.ts

## Question
UserApi.acceptTerms posts on behalf of the session with no argument; can an attacker trigger Privy constructor from app code paths so terms are accepted without the user acting?

## Target
- File/function: [src/client/Privy.ts](src/client/Privy.ts) - Privy constructor, initialize, getAccessToken, getIdentityToken, setMessagePoster, fetchPrivyRoute, getCompiledPath, track
- Entrypoint: new Privy({appId, clientId, sessions, storage, ...}) and privy.fetchPrivyRoute(...)
- Attacker controls: constructor options, arbitrary route+body via fetchPrivyRoute, message poster injection
- Exploit idea: Call the method directly and observe the user object change.
- Invariant to test: State-changing user operations must require an explicit user action signal.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: assert Privy constructor is not reachable from any automatic initialization path.
