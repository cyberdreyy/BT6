[1](#0-0)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/vesting.move (L61-80)
```text
    /// Withdrawal address is invalid.
    const EINVALID_WITHDRAWAL_ADDRESS: u64 = 1;
    /// Vesting schedule cannot be empty.
    const EEMPTY_VESTING_SCHEDULE: u64 = 2;
    /// Vesting period cannot be 0.
    const EZERO_VESTING_SCHEDULE_PERIOD: u64 = 3;
    /// Shareholders list cannot be empty.
    const ENO_SHAREHOLDERS: u64 = 4;
    /// The length of shareholders and shares lists don't match.
    const ESHARES_LENGTH_MISMATCH: u64 = 5;
    /// Vesting cannot start before or at the current block timestamp. Has to be in the future.
    const EVESTING_START_TOO_SOON: u64 = 6;
    /// The signer is not the admin of the vesting contract.
    const ENOT_ADMIN: u64 = 7;
    /// Vesting contract needs to be in active state.
    const EVESTING_CONTRACT_NOT_ACTIVE: u64 = 8;
    /// Admin can only withdraw from an inactive (paused or terminated) vesting contract.
    const EVESTING_CONTRACT_STILL_ACTIVE: u64 = 9;
    /// No vesting contract found at provided address.
    const EVESTING_CONTRACT_NOT_FOUND: u64 = 10;
```
