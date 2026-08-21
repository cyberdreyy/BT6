# Q2379: wallet-api path compiled from route templates in types.ts

## Question
getCompiledPath interpolates wallet_id into the route path before it is signed; can an attacker supply a wallet_id containing path separators so PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') signs and calls a different endpoint?

## Target
- File/function: [src/wallet-api/types.ts](src/wallet-api/types.ts) - PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry'), 1800000ms window
- Entrypoint: every signed wallet-api envelope
- Attacker controls: the expiry value chosen by the client clock
- Exploit idea: Pass a wallet id containing '/' or '%2F'.
- Invariant to test: Path parameters must be encoded before compilation and signing.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a separator-bearing wallet id to PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') and assert encoding or rejection.
