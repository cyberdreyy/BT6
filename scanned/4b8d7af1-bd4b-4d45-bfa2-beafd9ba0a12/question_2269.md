# Q2269: no response signature verification in types.ts

## Question
The wallet-api response is consumed after only a method-name comparison; can an attacker return a response through PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') whose signature field is arbitrary and have it used or broadcast?

## Target
- File/function: [src/wallet-api/types.ts](src/wallet-api/types.ts) - PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry'), 1800000ms window
- Entrypoint: every signed wallet-api envelope
- Attacker controls: the expiry value chosen by the client clock
- Exploit idea: Return an arbitrary signature and observe it flowing to the caller.
- Invariant to test: Responses carrying signatures must be verified against the request and the wallet key.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: return a bogus signature from PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry')'s route and assert verification fails.
