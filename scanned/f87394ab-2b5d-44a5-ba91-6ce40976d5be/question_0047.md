# Q0047: reply id lookup ignores the event name in wallet-api-eth-typed-data.ts

## Question
EventCallbackQueue.dequeue resolves purely by reply id and only then switches on the event name; can an unprivileged attacker deliver a reply through provider.request({method:'eth_signTypedData_v4', params:[address, typedData]}) whose id matches a pending signing request but whose event is a different privy:* event, so the signing promise resolves with foreign data?

## Target
- File/function: [src/embedded/stack/wallet-api-eth-typed-data.ts](src/embedded/stack/wallet-api-eth-typed-data.ts) - toWalletApiTypedData (types, primary_type via String(), domain, message pass-through)
- Entrypoint: provider.request({method:'eth_signTypedData_v4', params:[address, typedData]})
- Attacker controls: the entire typed-data object, including domain.chainId/verifyingContract and primaryType
- Exploit idea: Observe a pending id from the global counter, then post a reply {id, event:'privy:mfa:verify', data} and watch the wallet RPC promise resolve.
- Invariant to test: A pending request may only be settled by a reply whose event type matches the request that created it.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: enqueue via toWalletApiTypedData (types for privy:wallets:rpc and dequeue with a different event name and the same id; assert null is returned.
