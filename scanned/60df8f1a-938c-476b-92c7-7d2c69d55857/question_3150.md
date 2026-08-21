# Q3150: json body serialised twice in sign-wallet-request.ts

## Question
PrivyInternal.fetch JSON.stringifies the body while the signature covers the pre-serialisation object; can an attacker exploit serialisation differences (key order, unicode escaping, number formatting) so SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) signs one byte string and sends another?

## Target
- File/function: [src/wallet-api/sign-wallet-request.ts](src/wallet-api/sign-wallet-request.ts) - SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken)
- Entrypoint: every wallet-api signature
- Attacker controls: which message string is handed to the user signer and what it commits to
- Exploit idea: Include unicode, large numbers and key orders that differ between canonicalize and JSON.stringify.
- Invariant to test: Signed and transmitted encodings must be byte-identical.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert canonicalize output and the transmitted body are byte-equal for SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken).
