# Q0265: singleton queue shared across Privy clients in session-signers.ts

## Question
The callback queue is a module-level singleton shared by every proxy instance; can an attacker in a multi-client or multi-user page make one client's reply settle another client's pending request via addSessionSigners (getWallet then updateWallet with additional_signers.concat)?

## Target
- File/function: [src/embedded/stack/session-signers.ts](src/embedded/stack/session-signers.ts) - addSessionSigners (getWallet then updateWallet with additional_signers.concat), removeSessionSigners
- Entrypoint: privy.embeddedWallet session-signer flows
- Attacker controls: signers array contents, concurrency against another add/remove, wallet object fields
- Exploit idea: Instantiate two clients, start an operation on each, and deliver one reply.
- Invariant to test: Callback state must be scoped per client instance.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: create two proxies, enqueue on both through addSessionSigners (getWallet then updateWallet with additional_signers.concat) and assert their callback maps are disjoint.
