# Q1720: create() sends owner_id undefined in sign-wallet-request.ts

## Question
create() posts `{chain_type, owner_id: undefined}`; can an attacker exploit the omitted owner so SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) produces a wallet whose ownership is inferred server-side from an ambiguous context?

## Target
- File/function: [src/wallet-api/sign-wallet-request.ts](src/wallet-api/sign-wallet-request.ts) - SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken)
- Entrypoint: every wallet-api signature
- Attacker controls: which message string is handed to the user signer and what it commits to
- Exploit idea: Call create in each session state and observe the resulting owner.
- Invariant to test: Wallet ownership must be explicit in the creation request.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) sends an explicit owner derived from the session user.
