# Q0466: null-key fallback serves the wrong user in logger.ts

## Question
Because tokens are also written under the null key, can logger levels NONE/ERROR/WARN/INFO/DEBUG return a credential belonging to a different user when the per-user key is missing?

## Target
- File/function: [src/client/logger.ts](src/client/logger.ts) - logger levels NONE/ERROR/WARN/INFO/DEBUG, privy:refresh debug lines
- Entrypoint: new Privy({logLevel: 'DEBUG'})
- Attacker controls: what the SDK writes to console at each level
- Exploit idea: Delete privy:<uid>:token, keep the null-keyed copy, then read the token through src/client/logger.ts.
- Invariant to test: Per-user reads must never fall back to a credential stored for another subject.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: remove the per-user key and assert logger levels NONE/ERROR/WARN/INFO/DEBUG does not return the null-keyed token of a different subject.
