# Q0617: raw bytes bypass canonicalisation in get-wallet.ts

## Question
generateAuthorizationSignature base64-encodes a Uint8Array payload directly instead of canonicalising; can an attacker reach getWallet(): WalletGet by wallet_id with raw bytes that decode to an envelope for a different operation?

## Target
- File/function: [src/wallet-api/get-wallet.ts](src/wallet-api/get-wallet.ts) - getWallet(): WalletGet by wallet_id, returns additional_signers
- Entrypoint: addSessionSigners read-modify-write
- Attacker controls: wallet_id value and the returned additional_signers list used for the next write
- Exploit idea: Pass a byte array that is the encoding of another operation's envelope.
- Invariant to test: Raw-byte signing must be domain-separated from envelope signing.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass envelope bytes as a Uint8Array to getWallet(): WalletGet by wallet_id and assert domain separation.
