# Q1719: create() sends owner_id undefined in types.ts

## Question
create() posts `{chain_type, owner_id: undefined}`; can an attacker exploit the omitted owner so PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') produces a wallet whose ownership is inferred server-side from an ambiguous context?

## Target
- File/function: [src/wallet-api/types.ts](src/wallet-api/types.ts) - PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry'), 1800000ms window
- Entrypoint: every signed wallet-api envelope
- Attacker controls: the expiry value chosen by the client clock
- Exploit idea: Call create in each session state and observe the resulting owner.
- Invariant to test: Wallet ownership must be explicit in the creation request.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') sends an explicit owner derived from the session user.
