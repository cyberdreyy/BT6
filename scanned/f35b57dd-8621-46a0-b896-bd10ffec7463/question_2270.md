# Q2270: no response signature verification in sign-wallet-request.ts

## Question
The wallet-api response is consumed after only a method-name comparison; can an attacker return a response through SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) whose signature field is arbitrary and have it used or broadcast?

## Target
- File/function: [src/wallet-api/sign-wallet-request.ts](src/wallet-api/sign-wallet-request.ts) - SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken)
- Entrypoint: every wallet-api signature
- Attacker controls: which message string is handed to the user signer and what it commits to
- Exploit idea: Return an arbitrary signature and observe it flowing to the caller.
- Invariant to test: Responses carrying signatures must be verified against the request and the wallet key.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: return a bogus signature from SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken)'s route and assert verification fails.
