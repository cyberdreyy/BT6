# Q3172: cluster rpc url overrides the default in moonpay.ts

## Question
getSolanaRpcEndpointForCluster returns the caller's rpcUrl when set; can an attacker supply a cluster through isSupportedChainIdForMoonpay so balance and mint checks are answered by an endpoint they control and the user funds the wrong account?

## Target
- File/function: [src/funding/moonpay.ts](src/funding/moonpay.ts) - isSupportedChainIdForMoonpay, chainToMoonpayCurrency (defaults to ETH_ETHEREUM on unknown chain), fundingMethodToMoonpayPaymentMethod
- Entrypoint: MoonPay funding parameter construction
- Attacker controls: chainId and asset arguments
- Exploit idea: Pass a cluster with a crafted rpcUrl and observe the reads driving the funding decision.
- Invariant to test: Value-bearing reads must use pinned endpoints.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass a crafted cluster to isSupportedChainIdForMoonpay and assert the pinned endpoint is used.
