# Q0049: reply id lookup ignores the event name in resolve.ts

## Question
EventCallbackQueue.dequeue resolves purely by reply id and only then switches on the event name; can an unprivileged attacker deliver a reply through new Privy({crypto}) whose id matches a pending signing request but whose event is a different privy:* event, so the signing promise resolves with foreign data?

## Target
- File/function: [src/crypto/resolve.ts](src/crypto/resolve.ts) - resolveCrypto: digest and randomUUID defaults from globalThis.crypto
- Entrypoint: new Privy({crypto})
- Attacker controls: the crypto object the integrator/app passes in, and fallback behaviour when subtle is unavailable
- Exploit idea: Observe a pending id from the global counter, then post a reply {id, event:'privy:mfa:verify', data} and watch the wallet RPC promise resolve.
- Invariant to test: A pending request may only be settled by a reply whose event type matches the request that created it.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: enqueue via resolveCrypto: digest and randomUUID defaults from globalThis.crypto for privy:wallets:rpc and dequeue with a different event name and the same id; assert null is returned.
