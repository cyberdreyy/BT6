[File: 'smart-contracts-poc/contracts/oracles/utils/TimeMs.sol -> Scope: Critical. FeedId, namespace remapping, pusher authorization, or signature replay bug lets an unprivileged actor overwrite or hijack oracle data used by pools.'] [Symbol: LazerConsumer / _verifyPayload / tsMs] Can a Pyth Lazer network operator under MICROSECOND_TIMESTAMP_OVERFLOW_STATE

### Citations

**File:** smart-contracts-poc/contracts/oracles/utils/TimeMs.sol (L1-38)
```text
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

type TimeMs is uint56;

error TimeMsOverflow(uint256 value);
error FutureTimestamp();
error ZeroTimestamp();

using {isAfter, revertIfAfterBlockTimeWithDrift, toSeconds, isZero, revertIfZero} for TimeMs global;

function toTimeMs(uint256 value) pure returns (TimeMs) {
    if (value > type(uint56).max) {
        revert TimeMsOverflow(value);
    }
    // forge-lint: disable-next-line(unsafe-typecast)
    return TimeMs.wrap(uint56(value));
}

function toSeconds(TimeMs t) pure returns (uint56) {
    return TimeMs.unwrap(t) / 1000;
}

function isAfter(TimeMs t0, TimeMs t1) pure returns (bool) {
    return TimeMs.unwrap(t0) > TimeMs.unwrap(t1);
}

function revertIfAfterBlockTimeWithDrift(TimeMs t0, uint256 drift) view {
    require(t0.toSeconds() <= block.timestamp + drift, FutureTimestamp());
}

function isZero(TimeMs t) pure returns (bool) {
    return TimeMs.unwrap(t) == 0;
}

function revertIfZero(TimeMs t) pure {
    require(!t.isZero(), ZeroTimestamp());
}
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L1-370)
```text
// SPDX-License-Identifier: BUSL-1.1
pragma solidity ^0.8.28;

import {ECDSA} from
