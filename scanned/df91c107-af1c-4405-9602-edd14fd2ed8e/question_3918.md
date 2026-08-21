# Q3918: signature over base64 of canonical json in update-wallet.ts

## Question
The signed message is base64(utf8(canonical json)); can an attacker construct a payload whose base64 form is also a valid envelope for another operation so a signature from updateWallet(): signs {version:1 is reinterpretable?

## Target
- File/function: [src/wallet-api/update-wallet.ts](src/wallet-api/update-wallet.ts) - updateWallet(): signs {version:1, url, method, headers:{privy-app-id}, body} with NO privy-request-expiry header
- Entrypoint: session signer add/remove
- Attacker controls: the body (additional_signers) and the resulting long-lived authorization signature
- Exploit idea: Search for payload pairs whose encodings overlap under the server's parsing rules.
- Invariant to test: Signed messages must carry an unambiguous type tag.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert updateWallet(): signs {version:1's signed message includes an explicit operation type tag.
