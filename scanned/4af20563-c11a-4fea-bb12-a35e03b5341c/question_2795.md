# Q2795: eth_sign and secp256k1_sign share a path in session-signers.ts

## Question
walletRpc maps eth_sign and secp256k1_sign to the same raw hash signing method; can an attacker use addSessionSigners (getWallet then updateWallet with additional_signers.concat) to obtain a raw-hash signature over a value the user believed was a display message?

## Target
- File/function: [src/embedded/stack/session-signers.ts](src/embedded/stack/session-signers.ts) - addSessionSigners (getWallet then updateWallet with additional_signers.concat), removeSessionSigners
- Entrypoint: privy.embeddedWallet session-signer flows
- Attacker controls: signers array contents, concurrency against another add/remove, wallet object fields
- Exploit idea: Submit a 32-byte hash-shaped string through the message path.
- Invariant to test: Raw hash signing must be visibly distinct from message signing.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert addSessionSigners (getWallet then updateWallet with additional_signers.concat) refuses raw-hash signing without an explicit raw-sign intent.
