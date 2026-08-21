# Q1938: getWallet result drives the next write in update-wallet.ts

## Question
getWallet returns additional_signers that addSessionSigners concatenates and writes back; can an attacker influence the read so updateWallet(): signs {version:1 writes back a signer set containing an entry they control?

## Target
- File/function: [src/wallet-api/update-wallet.ts](src/wallet-api/update-wallet.ts) - updateWallet(): signs {version:1, url, method, headers:{privy-app-id}, body} with NO privy-request-expiry header
- Entrypoint: session signer add/remove
- Attacker controls: the body (additional_signers) and the resulting long-lived authorization signature
- Exploit idea: Return an extra signer in the read response and observe it persisted by the subsequent write.
- Invariant to test: Read-modify-write of authorization state must validate every entry before rewriting.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: inject an extra signer into updateWallet(): signs {version:1's read stub and assert it is not written back.
