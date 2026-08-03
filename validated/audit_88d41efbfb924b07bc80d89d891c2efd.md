[1](#0-0)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/stake.move (L1-20)
```text
///
/// Validator lifecycle:
/// 1. Prepare a validator node set up and call stake::initialize_validator
/// 2. Once ready to deposit stake (or have funds assigned by a staking service in exchange for ownership capability),
/// call stake::add_stake (or *_with_cap versions if called from the staking service)
/// 3. Call stake::join_validator_set (or _with_cap version) to join the active validator set. Changes are effective in
/// the next epoch.
/// 4. Validate and gain rewards. The stake will automatically be locked up for a fixed duration (set by governance) and
/// automatically renewed at expiration.
/// 5. At any point, if the validator operator wants to update the consensus key or network/fullnode addresses, they can
/// call stake::rotate_consensus_key and stake::update_network_and_fullnode_addresses. Similar to changes to stake, the
/// changes to consensus key/network/fullnode addresses are only effective in the next epoch.
/// 6. Validator can request to unlock their stake at any time. However, their stake will only become withdrawable when
/// their current lockup expires. This can be at most as long as the fixed lockup duration.
/// 7. After exiting, the validator can either explicitly leave the validator set by calling stake::leave_validator_set
/// or if their stake drops below the min required, they would get removed at the end of the epoch.
/// 8. Validator can always rejoin the validator set by going through steps 2-3 again.
/// 9. An owner can always switch operators by calling stake::set_operator.
/// 10. An owner can always switch designated voter by calling stake::set_designated_voter.
module aptos_framework::stake {
```
