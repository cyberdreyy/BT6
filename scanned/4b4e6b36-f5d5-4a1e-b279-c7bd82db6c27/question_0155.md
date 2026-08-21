# Q0155: predictable global request ids in session-signers.ts

## Question
Request ids come from a module-level counter emitting id-0, id-1, ...; can an attacker predict the next id and pre-deliver a reply through privy.embeddedWallet session-signer flows so their data settles the victim's next operation?

## Target
- File/function: [src/embedded/stack/session-signers.ts](src/embedded/stack/session-signers.ts) - addSessionSigners (getWallet then updateWallet with additional_signers.concat), removeSessionSigners
- Entrypoint: privy.embeddedWallet session-signer flows
- Attacker controls: signers array contents, concurrency against another add/remove, wallet object fields
- Exploit idea: Count the ids issued so far, then post a reply for the next id before the real iframe answers.
- Invariant to test: Reply correlation must use unguessable, per-instance identifiers.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: run two operations through addSessionSigners (getWallet then updateWallet with additional_signers.concat) and assert the ids are not sequentially predictable.
