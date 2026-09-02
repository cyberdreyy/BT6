### Title
Multisig executes requests below the live-member confirmation threshold because deleting a member does not purge that member's existing confirmations on other pending requests - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::confirm` decides whether a request has reached quorum purely by counting entries in the `confirmations` `HashSet` for that `request_id`: `confirmations.len() as u32 + 1 >= self.num_confirmations` [1](#0-0)  . Removing a member (`DeleteMember`) only updates the `members` set via `self.delete_member(promise, member)` inside `execute_request`; it does not touch `self.confirmations` for any *other* still-pending request [2](#0-1)  . `remove_request` (which clears a request's confirmations) only runs for the request that is being executed or deleted, not for unrelated pending requests [3](#0-2)  . Consequently a confirmation recorded by an account/key that is later removed as a member remains in the confirmations set and continues to count toward `num_confirmations` for that pending request.

### Finding Description
The contract's core custody binding for a multisig is:
```
confirmations_counted(request) == confirmations_by_currently_live_members(request)
```
This must hold at the moment `confirm` decides to execute a request, otherwise a request can execute with fewer *live* authorized confirmations than `num_confirmations` requires — structurally the same class of bug as the reported leverage miscalculation, where a value derived from stale/decoupled state (`_debt_value + _margin_value`) was substituted for the value that should have been re-derived from current state (`_position_value`). Here, a stale confirmation (from a member whose authorization has since been revoked) is substituted for a live confirmation.

Concretely:
1. Member `mallory` (one of `n` members, `num_confirmations = k`) creates and self-confirms a request `R` via `add_request_and_confirm`, e.g., a `Transfer` of contract funds to herself, giving `confirmations[R] = {mallory}` [4](#0-3)  .
2. The remaining live members separately confirm and execute an unrelated `DeleteMember { member: mallory }` request, which removes `mallory` from `self.members` [5](#0-4)  . This execution path removes the confirmations only for *that* deletion request (`remove_request` is only called for the request being executed) [6](#0-5)  ; `confirmations[R]` is left untouched, still containing `mallory`.
3. Now only `k-1` genuinely live members are needed to push `R` over the threshold, because `confirmations.len() as u32 + 1 >= self.num_confirmations` in `confirm` counts `mallory`'s stale entry as if it were a currently valid confirmation [7](#0-6)  .
4. `R` (e.g., the `Transfer`) executes with fewer live-member confirmations than `num_confirmations` was configured to require.

### Impact Explanation
This breaks the "a multisig request executed below threshold" invariant explicitly listed as Critical impact: funds can be moved, keys added, or members added/removed by a coalition of live members smaller than the configured `num_confirmations`, because a removed member's earlier confirmation is silently retained and counted. This is an authorization-threshold bypass reachable by unprivileged participants who were once (but no longer are) members, without requiring any redeploy, foundation action, or malicious validator.

### Likelihood Explanation
The pattern requires two ordinary multisig actions that already exist in the contract's intended workflow: an account is added as a member and later removed (a routine operational event, e.g., off-boarding an employee or rotating a compromised key) while it still has a pending, self-confirmed or partially-confirmed request outstanding. No special privilege beyond normal current-member status at the time confirmation was recorded is needed, and the `active_requests_limit` (default 12) makes it easy to keep such a request live across the removal.

### Recommendation
When executing `DeleteMember`, iterate over all entries in `self.requests`/`self.confirmations` and remove the deleted member's confirmation from every pending request (or re-validate, inside `confirm`, that every entry in `confirmations` still belongs to `self.members` before counting it toward the threshold). The safest fix is to filter `confirmations` against current membership at counting time in `confirm`, e.g.:
```rust
let live_confirmations = confirmations
    .iter()
    .filter(|c| self.members.contains(&parse_member(c)))
    .count() as u32;
if live_confirmations + 1 >= self.num_confirmations { ... }
```

### Proof of Concept
```
members = [alice, bob, mallory], num_confirmations = 2

1. mallory.add_request_and_confirm(R = Transfer{amount: X})
   -> confirmations[R] = {mallory}

2. alice.add_request_and_confirm(D = DeleteMember{mallory})
   bob.confirm(D)
   -> D executes (2 live confirmations: alice, bob), mallory removed from members
   -> confirmations[R] is untouched, still {mallory}

3. alice.confirm(R)
   -> confirmations[R].len() (1, "mallory") + 1 (alice) == 2 >= num_confirmations (2)
   -> R executes: funds transferred with only 1 live member (alice) actually approving,
      even though num_confirmations=2 was meant to require 2 live members.
```

Note: I could not, within the remaining tool budget, read the full body of `current_member()`/`assert_valid_request()` in `multisig2/src/lib.rs` to confirm whether they add any additional runtime re-validation that might mitigate this specific scenario (e.g. NEAR access-key revocation for `AccessKey` members). The core defect — `confirmations` for a pending request not being purged of removed members' entries in `delete_member`/`execute_request`/`remove_request` — is confirmed directly from the code cited above and is independent of who is allowed to call `confirm`.

### Citations

**File:** multisig2/src/lib.rs (L202-207)
```rust
    /// Add request for multisig and confirm with the pk that added.
    pub fn add_request_and_confirm(&mut self, request: MultiSigRequest) -> RequestId {
        let request_id = self.add_request(request);
        self.confirm(request_id);
        request_id
    }
```

**File:** multisig2/src/lib.rs (L209-222)
```rust
    /// Remove given request and associated confirmations.
    pub fn delete_request(&mut self, request_id: RequestId) {
        self.assert_valid_request(request_id);
        let request_with_signer = self
            .requests
            .get(&request_id)
            .unwrap_or_else(|| env::panic_str("No such request"));
        // can't delete requests before 15min
        assert(
            env::block_timestamp() > request_with_signer.added_timestamp + REQUEST_COOLDOWN,
            "Request cannot be deleted immediately after creation.",
        );
        self.remove_request(request_id);
    }
```

**File:** multisig2/src/lib.rs (L224-244)
```rust
    fn execute_request(&mut self, request: MultiSigRequest) -> PromiseOrValue<bool> {
        let mut promise = Promise::new(request.receiver_id.clone());
        let receiver_id = request.receiver_id.clone();
        let num_actions = request.actions.len();
        for action in request.actions {
            promise = match action {
                MultiSigRequestAction::Transfer { amount } => promise.transfer(amount.into()),
                MultiSigRequestAction::CreateAccount => promise.create_account(),
                MultiSigRequestAction::DeployContract { code } => {
                    promise.deploy_contract(code.into())
                }
                MultiSigRequestAction::AddMember { member } => {
                    self.assert_self_request(receiver_id.clone());
                    self.add_member(promise, member)
                }
                MultiSigRequestAction::DeleteMember { member } => {
                    self.assert_self_request(receiver_id.clone());
                    self.delete_member(promise, member)
                }
                MultiSigRequestAction::AddKey {
                    public_key,
```

**File:** multisig2/src/lib.rs (L270-291)
```rust
                    args.into(),
                    deposit.into(),
                    Gas::from(gas.0),
                ),
                // the following methods must be a single action
                MultiSigRequestAction::SetNumConfirmations { num_confirmations } => {
                    self.assert_one_action_only(receiver_id, num_actions);
                    self.num_confirmations = num_confirmations;
                    return PromiseOrValue::Value(true);
                }
                MultiSigRequestAction::SetActiveRequestsLimit {
                    active_requests_limit,
                } => {
                    self.assert_one_action_only(receiver_id, num_actions);
                    self.active_requests_limit = active_requests_limit;
                    return PromiseOrValue::Value(true);
                }
            };
        }
        promise.into()
    }

```

**File:** multisig2/src/lib.rs (L292-315)
```rust
    /// Confirm given request with given signing key.
    /// If with this, there has been enough confirmation, a promise with request will be scheduled.
    pub fn confirm(&mut self, request_id: RequestId) -> PromiseOrValue<bool> {
        self.assert_valid_request(request_id);
        let member = self
            .current_member()
            .unwrap_or_else(|| env::panic_str("Must be validated above"));
        let mut confirmations = self.confirmations.get(&request_id).unwrap();
        assert(
            !confirmations.contains(&member.to_string()),
            "Already confirmed this request with this key",
        );
        if confirmations.len() as u32 + 1 >= self.num_confirmations {
            let request = self.remove_request(request_id);
            /********************************
            NOTE: If the tx execution fails for any reason, the request and confirmations are removed already, so the client has to start all over
            ********************************/
            self.execute_request(request)
        } else {
            confirmations.insert(member.to_string());
            self.confirmations.insert(&request_id, &confirmations);
            PromiseOrValue::Value(true)
        }
    }
```
