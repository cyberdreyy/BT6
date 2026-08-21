# Q2599: chain_type chosen by the caller in types.ts

## Question
The signed body includes a caller-supplied chain_type; can an attacker mismatch chain_type against the wallet through PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') so a signature valid on one chain is produced for a wallet on another?

## Target
- File/function: [src/wallet-api/types.ts](src/wallet-api/types.ts) - PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry'), 1800000ms window
- Entrypoint: every signed wallet-api envelope
- Attacker controls: the expiry value chosen by the client clock
- Exploit idea: Submit an ethereum method for a solana wallet id.
- Invariant to test: Chain type must be derived from the wallet record, not the request.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: mismatch chain_type and wallet in PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') and assert rejection.
