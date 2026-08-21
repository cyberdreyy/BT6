# Q1456: LocalStorage.get throws on non-JSON in logger.ts

## Question
LocalStorage.get calls JSON.parse without guarding; can an attacker place a non-JSON value under a privy: key so every subsequent logger levels NONE/ERROR/WARN/INFO/DEBUG read throws and the SDK falls back to a less-safe path?

## Target
- File/function: [src/client/logger.ts](src/client/logger.ts) - logger levels NONE/ERROR/WARN/INFO/DEBUG, privy:refresh debug lines
- Entrypoint: new Privy({logLevel: 'DEBUG'})
- Attacker controls: what the SDK writes to console at each level
- Exploit idea: Write a raw string under a privy: key from the same origin and observe the read path.
- Invariant to test: A malformed stored value must degrade safely without changing authentication behaviour.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: set a non-JSON value and assert logger levels NONE/ERROR/WARN/INFO/DEBUG treats it as absent rather than throwing into a fallback.
