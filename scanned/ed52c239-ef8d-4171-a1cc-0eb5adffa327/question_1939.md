# Q1939: getWallet result drives the next write in types.ts

## Question
getWallet returns additional_signers that addSessionSigners concatenates and writes back; can an attacker influence the read so PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') writes back a signer set containing an entry they control?

## Target
- File/function: [src/wallet-api/types.ts](src/wallet-api/types.ts) - PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry'), 1800000ms window
- Entrypoint: every signed wallet-api envelope
- Attacker controls: the expiry value chosen by the client clock
- Exploit idea: Return an extra signer in the read response and observe it persisted by the subsequent write.
- Invariant to test: Read-modify-write of authorization state must validate every entry before rewriting.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: inject an extra signer into PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry')'s read stub and assert it is not written back.
