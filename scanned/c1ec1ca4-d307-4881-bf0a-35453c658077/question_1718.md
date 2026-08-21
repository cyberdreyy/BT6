# Q1718: create() sends owner_id undefined in update-wallet.ts

## Question
create() posts `{chain_type, owner_id: undefined}`; can an attacker exploit the omitted owner so updateWallet(): signs {version:1 produces a wallet whose ownership is inferred server-side from an ambiguous context?

## Target
- File/function: [src/wallet-api/update-wallet.ts](src/wallet-api/update-wallet.ts) - updateWallet(): signs {version:1, url, method, headers:{privy-app-id}, body} with NO privy-request-expiry header
- Entrypoint: session signer add/remove
- Attacker controls: the body (additional_signers) and the resulting long-lived authorization signature
- Exploit idea: Call create in each session state and observe the resulting owner.
- Invariant to test: Wallet ownership must be explicit in the creation request.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert updateWallet(): signs {version:1 sends an explicit owner derived from the session user.
