I was unable to complete the full investigation required by this audit task. Here is what I examined and what remains unexamined:

**Files examined:**
- `smart-contracts-poc/contracts/PriceProvider.sol` — `setConfidenceParam` is factory-gated with a 1-minute cooldown; `getBidAndAskPrice` has staleness, price guard, and bid<ask invariant checks. No permissionless write path found. [1](#0-0) 
- `smart-contracts-poc/contracts/AnchoredPriceProvider.sol` — `setSource` and `setConfidenceParam` are factory-gated; `_readSource` is gas-bounded; the band clamp in `_computeBidAsk` enforces `bid < ask` and clips any source output to `[refBid, refAsk]`. No permissionless write path found. [2](#0-1) 
- `smart-contracts-poc/contracts/ProtectedPriceProvider.sol` — identical access-control pattern to `PriceProvider`. [3](#0-2) 

**Critical directories NOT examined** (tool-call budget exhausted before reaching them):

- `smart-contracts-poc/contracts/oracles/compressed/` — This is where `CompressedOracleV1`, `feedIdOf`, `Codebook256`, `U64x32`, `TimeMs`, and the pusher-push entry point live. The audit pivots explicitly flag this as the primary attack surface (permissionless `push`, packed observations, codebook spread indexes, namespace remapping). 
- `smart-contracts-poc/contracts/oracles/providers/` — Chainlink and Pyth provider adapters with sequencer-uptime, decimals, and maxTimeDrift bindings. 
- Pusher delegation logic (`allowPushers`, `allowContractPushers`, `revokePusher`, EIP-191 signatures, deadline/replay guards) — not located.

**Conclusion:** The three provider contracts I read are properly guarded and do not contain a permissionless analog to the EulerEarn rebalance bug. However, the compressed oracle push path and pusher delegation code — the most likely location for such an analog per the audit pivots — were not reachable within the available tool iterations. A definitive finding or rejection requires reading those files. To complete this audit, start a Devin session with direct filesystem access to `smart-contracts-poc/contracts/oracles/compressed/` and the pusher delegation contracts.

### Citations

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L92-104)
```text
    function setConfidenceParam(uint256 newValue) external {
        require(msg.sender == factory, OnlyFactory());
        if (newValue > CONFIDENCE_MAX) {
            revert ConfidenceParamOutOfBounds();
        }
        if (block.timestamp < lastConfidenceUpdate + CONFIDENCE_COOLDOWN) {
            revert CooldownNotElapsed();
        }

        confidenceParam = newValue;
        lastConfidenceUpdate = block.timestamp;
        emit ConfidenceParamSet(newValue);
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L340-348)
```text
        // 8. Clamp: out-of-band custom quotes are clipped silently to the band edge.
        //    bid ≤ refBid < refAsk ≤ ask, so bid < ask holds by construction.
        uint256 bidOut = Math.min(refBid, cBid);
        uint256 askOut = Math.max(refAsk, cAsk);
        if (bidOut == 0 || bidOut >= askOut) {
            return (0, type(uint128).max);
        }

        return (uint128(bidOut), uint128(askOut));
```

**File:** smart-contracts-poc/contracts/ProtectedPriceProvider.sol (L95-107)
```text
    function setConfidenceParam(uint256 newValue) external override {
        require(msg.sender == factory, OnlyFactory());
        if (newValue > CONFIDENCE_MAX) {
            revert ConfidenceParamOutOfBounds();
        }
        if (block.timestamp < lastConfidenceUpdate + CONFIDENCE_COOLDOWN) {
            revert CooldownNotElapsed();
        }

        confidenceParam = newValue;
        lastConfidenceUpdate = block.timestamp;
        emit ConfidenceParamSet(newValue);
    }
```
