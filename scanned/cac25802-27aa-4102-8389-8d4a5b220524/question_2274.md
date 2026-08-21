# Q2274: read_only flag is the only authorization gate in getProviderAccessTokenOrRelink.ts

## Question
sendCrossAppRequest rejects only when the connection is marked read_only; can an attacker influence the connections response so getProviderAccessTokenOrRelink: cached token from storage else relink treats a read-only connection as transactable?

## Target
- File/function: [src/action/crossApp/wallet/utils/getProviderAccessTokenOrRelink.ts](src/action/crossApp/wallet/utils/getProviderAccessTokenOrRelink.ts) - getProviderAccessTokenOrRelink: cached token from storage else relink
- Entrypoint: cross-app wallet operations
- Attacker controls: the cached privy:cross-app:<appId> value and its decoded expiry
- Exploit idea: Return a connection without the read_only flag.
- Invariant to test: Transaction authority must be established server-side, not by a client-visible flag.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: omit read_only in getProviderAccessTokenOrRelink: cached token from storage else relink's stub and assert the SDK still requires explicit authority.
