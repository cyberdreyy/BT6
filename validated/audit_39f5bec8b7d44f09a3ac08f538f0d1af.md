### No vulnerability found for this question.

**Reasoning:**

The premise fails at the first gate: the staking pool's `vote` proxy is an owner-only method, not something an unprivileged delegator can invoke. [1](#0-0) 

`StakingContract::vote` calls `self.assert_owner()` before forwarding to `ext_voting::vote`, so only the pool's owner — not a random depositor/delegator — can cause the pool account to cast a vote. An "unprivileged attacker" per the rules (no owner privileges, no key on the victim account) cannot trigger this path at all.

Even setting aside the privilege gate, the underlying accounting is sound. In `VotingContract::vote` and `ping`, the recorded voter is always `env::predecessor_account_id()`, and its recorded stake is always `env::validator_stake(&account_id)` — i.e., whatever NEAR protocol reports as that account's actual validator stake for the epoch. [2](#0-1) [3](#0-2) 

When a staking pool votes, the "voter" recorded is genuinely the pool account, because the pool — not the individual delegator — is the actual on-chain validator whose stake NEAR consensus tracks; this is documented intended behavior ("Vote on behalf of the pool"), not a mismatch. [4](#0-3) 

As for "stake grew sharply after it voted": validator stake is fixed for the duration of an epoch by NEAR protocol consensus, and `ping()` recomputes every recorded voter's `account_current_stake` via `env::validator_stake` at every epoch boundary, dropping the entry entirely if the account is no longer a validator. So there is no window where the recorded vote's weight diverges from the current epoch's actual validator stake for that account — the two named values (`votes[account_id]` and `env::validator_stake(account_id)` for the active epoch) are kept synchronized by `ping`, and `check_result` only fires once total voted stake exceeds 2/3 of `env::validator_total_stake()`, both computed from the same protocol source. [5](#0-4) 

No code path lets an unprivileged caller cause a stake-to-voter mismatch that inflates `total_voted_stake` beyond what the protocol actually attributes to real validators, so the claimed invariant break does not hold, and no NEAR/lockup tokens can be released early through this mechanism.

### Citations

**File:** staking-pool/src/lib.rs (L450-460)
```rust
    /// Owner's method.
    /// Calls `vote(is_vote)` on the given voting contract account ID on behalf of the pool.
    pub fn vote(&mut self, voting_account_id: AccountId, is_vote: bool) -> Promise {
        self.assert_owner();
        assert!(
            env::is_valid_account_id(voting_account_id.as_bytes()),
            "Invalid voting account ID"
        );

        ext_voting::vote(is_vote, &voting_account_id, NO_DEPOSIT, VOTE_GAS)
    }
```

**File:** voting/src/lib.rs (L46-62)
```rust
    pub fn ping(&mut self) {
        assert!(self.result.is_none(), "Voting has already ended");
        let cur_epoch_height = env::epoch_height();
        if cur_epoch_height != self.last_epoch_height {
            let votes = std::mem::take(&mut self.votes);
            self.total_voted_stake = 0;
            for (account_id, _) in votes {
                let account_current_stake = env::validator_stake(&account_id);
                self.total_voted_stake += account_current_stake;
                if account_current_stake > 0 {
                    self.votes.insert(account_id, account_current_stake);
                }
            }
            self.check_result();
            self.last_epoch_height = cur_epoch_height;
        }
    }
```

**File:** voting/src/lib.rs (L64-74)
```rust
    /// Check whether the voting has ended.
    fn check_result(&mut self) {
        assert!(
            self.result.is_none(),
            "check result is called after result is already set"
        );
        let total_stake = env::validator_total_stake();
        if self.total_voted_stake > 2 * total_stake / 3 {
            self.result = Some(U64::from(env::block_timestamp()));
        }
    }
```

**File:** voting/src/lib.rs (L78-103)
```rust
    pub fn vote(&mut self, is_vote: bool) {
        self.ping();
        if self.result.is_some() {
            return;
        }
        let account_id = env::predecessor_account_id();
        let account_stake = if is_vote {
            let stake = env::validator_stake(&account_id);
            assert!(stake > 0, "{} is not a validator", account_id);
            stake
        } else {
            0
        };
        let voted_stake = self.votes.remove(&account_id).unwrap_or_default();
        assert!(
            voted_stake <= self.total_voted_stake,
            "invariant: voted stake {} is more than total voted stake {}",
            voted_stake,
            self.total_voted_stake
        );
        self.total_voted_stake = self.total_voted_stake + account_stake - voted_stake;
        if account_stake > 0 {
            self.votes.insert(account_id, account_stake);
            self.check_result();
        }
    }
```

**File:** staking-pool/README.md (L118-126)
```markdown
## Owner-only methods

Contract owner can do the following:
- Change public staking key. This action restakes with the new key.
- Change reward fee fraction.
- Vote on behalf of the pool. This is needed for the NEAR chain governance, and can be discussed in the following NEP: https://github.com/nearprotocol/NEPs/pull/62
- Pause and resume staking. When paused, the pool account unstakes everything (stakes 0) and doesn't restake.
It doesn't affect the staking shares or reward distribution. Pausing is useful for node maintenance. Note, the contract is not paused by default.

```
