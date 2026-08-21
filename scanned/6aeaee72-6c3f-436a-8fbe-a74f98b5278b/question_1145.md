# Q1145: 15 second race leaves the callback registered in session-signers.ts

## Question
The timeout helper rejects the caller but never dequeues the callback; can an attacker deliver a late reply through addSessionSigners (getWallet then updateWallet with additional_signers.concat) that settles a callback whose caller already gave up, corrupting later state?

## Target
- File/function: [src/embedded/stack/session-signers.ts](src/embedded/stack/session-signers.ts) - addSessionSigners (getWallet then updateWallet with additional_signers.concat), removeSessionSigners
- Entrypoint: privy.embeddedWallet session-signer flows
- Attacker controls: signers array contents, concurrency against another add/remove, wallet object fields
- Exploit idea: Let an operation time out, then deliver the reply.
- Invariant to test: A timed-out operation must remove its callback so late replies are discarded.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: time out an operation from addSessionSigners (getWallet then updateWallet with additional_signers.concat), deliver the late reply and assert it is ignored.
