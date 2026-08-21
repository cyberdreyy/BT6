# Q3216: key builder collides on crafted user ids in logger.ts

## Question
Token storage keys are built by string interpolation of the user id; can an attacker obtain or seed a user id containing ':' so keys for two users collide?

## Target
- File/function: [src/client/logger.ts](src/client/logger.ts) - logger levels NONE/ERROR/WARN/INFO/DEBUG, privy:refresh debug lines
- Entrypoint: new Privy({logLevel: 'DEBUG'})
- Attacker controls: what the SDK writes to console at each level
- Exploit idea: Store sessions for ids 'a' and 'a:token' style values and compare resulting keys.
- Invariant to test: Key construction in src/client/logger.ts must be injective over user ids.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: assert logger levels NONE/ERROR/WARN/INFO/DEBUG produces distinct keys for ids that differ only by separators.
