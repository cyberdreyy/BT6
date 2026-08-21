# Q2666: user.get returns refreshed foreign user in logger.ts

## Question
UserApi.get returns whatever refreshSession yields; can an attacker interleave a switch so logger levels NONE/ERROR/WARN/INFO/DEBUG returns another user's profile to code that just authorised an action for the first user?

## Target
- File/function: [src/client/logger.ts](src/client/logger.ts) - logger levels NONE/ERROR/WARN/INFO/DEBUG, privy:refresh debug lines
- Entrypoint: new Privy({logLevel: 'DEBUG'})
- Attacker controls: what the SDK writes to console at each level
- Exploit idea: Switch active user during the in-flight refresh and inspect the returned user.
- Invariant to test: A user read must be atomic with respect to active-user changes.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: switch users mid-refresh and assert logger levels NONE/ERROR/WARN/INFO/DEBUG throws rather than returning the other profile.
