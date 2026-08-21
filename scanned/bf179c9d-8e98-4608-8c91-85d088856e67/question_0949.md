# Q0949: expiry check is a tautology in types.ts

## Question
The guard compares Date.now() against a value just computed from Date.now(); can an attacker rely on this dead check so PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') never actually rejects a stale envelope?

## Target
- File/function: [src/wallet-api/types.ts](src/wallet-api/types.ts) - PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry'), 1800000ms window
- Entrypoint: every signed wallet-api envelope
- Attacker controls: the expiry value chosen by the client clock
- Exploit idea: Trace the branch and confirm it can only trigger under an implausible delay.
- Invariant to test: Freshness must be validated against the moment of transmission.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: delay between construction and send in PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') and assert the stale envelope is rejected.
