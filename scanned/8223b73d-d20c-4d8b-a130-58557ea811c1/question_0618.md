# Q0618: raw bytes bypass canonicalisation in update-wallet.ts

## Question
generateAuthorizationSignature base64-encodes a Uint8Array payload directly instead of canonicalising; can an attacker reach updateWallet(): signs {version:1 with raw bytes that decode to an envelope for a different operation?

## Target
- File/function: [src/wallet-api/update-wallet.ts](src/wallet-api/update-wallet.ts) - updateWallet(): signs {version:1, url, method, headers:{privy-app-id}, body} with NO privy-request-expiry header
- Entrypoint: session signer add/remove
- Attacker controls: the body (additional_signers) and the resulting long-lived authorization signature
- Exploit idea: Pass a byte array that is the encoding of another operation's envelope.
- Invariant to test: Raw-byte signing must be domain-separated from envelope signing.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass envelope bytes as a Uint8Array to updateWallet(): signs {version:1 and assert domain separation.
