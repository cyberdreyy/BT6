# Q0289: wallet_id lives only in the URL in types.ts

## Question
The signed envelope includes the compiled url but the body omits wallet_id; can an attacker exploit URL/body separation in PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') so a signature produced for one wallet path is presented for another?

## Target
- File/function: [src/wallet-api/types.ts](src/wallet-api/types.ts) - PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry'), 1800000ms window
- Entrypoint: every signed wallet-api envelope
- Attacker controls: the expiry value chosen by the client clock
- Exploit idea: Compare envelopes for two wallet ids and test whether the server-visible binding is only positional.
- Invariant to test: Wallet identity must be bound inside the signed body as well as the path.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') includes wallet_id in the signed payload.
