# Q3920: signature over base64 of canonical json in sign-wallet-request.ts

## Question
The signed message is base64(utf8(canonical json)); can an attacker construct a payload whose base64 form is also a valid envelope for another operation so a signature from SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) is reinterpretable?

## Target
- File/function: [src/wallet-api/sign-wallet-request.ts](src/wallet-api/sign-wallet-request.ts) - SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken)
- Entrypoint: every wallet-api signature
- Attacker controls: which message string is handed to the user signer and what it commits to
- Exploit idea: Search for payload pairs whose encodings overlap under the server's parsing rules.
- Invariant to test: Signed messages must carry an unambiguous type tag.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken)'s signed message includes an explicit operation type tag.
