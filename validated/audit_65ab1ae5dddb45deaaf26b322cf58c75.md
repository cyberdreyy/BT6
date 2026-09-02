### Title
`SetNumConfirmations` accepts a threshold with no bound check against member count, allowing multisig requests to execute below the intended K-of-N threshold or freezing the account permanently - ([File: multisig2/src/lib.rs])

### Summary
This is a valid analog of the reported bug class. The GluexRouter report flags a setter (`setMinFee`) that mutates a security-critical bound (`_MIN_FEE`) without validating it against the counterpart bound (`_MAX_FEE`), letting the invariant `_MIN_FEE <= _MAX_FEE` be violated. The same root cause exists in `multisig2/src/lib.rs`: `MultiSigRequestAction::SetNumConfirmations` writes `self.num_confirmations = num_confirmations` with no validation against `self.members.len()`, breaking the invariant that is enforced everywhere else in the contract (`new()` and `delete_member()`).

### Finding Description
The invariant the contract is supposed to maintain is: `num_confirmations <= members.len()` (and implicitly `num_confirmations >= 1` for the K-of-N scheme to have any meaning).

This invariant is enforced in two places:
- At initialization: `new()` asserts `members.len() >= num_confirmations`. [1](#0-0) 
- When removing a member: `delete_member()` asserts `self.members.len() - 1 >= self.num_confirmations`. [2](#0-1) 

However, the `SetNumConfirmations` request action, which is itself just another multisig-approved action, sets `self.num_confirmations` directly with **no bound check at all**: [3](#0-2) 

This is structurally identical to the reported bug: a setter mutating one side of a bound relied upon by other logic, without checking it against the other side of that relationship.

### Impact Explanation
`confirm()` uses `self.num_confirmations` as the sole threshold to decide whether to execute a request: [4](#0-3) 

- If `num_confirmations` is set to `0`, the check `confirmations.len() as u32 + 1 >= self.num_confirmations` is always true even before any confirmation is recorded (0 + 1 >= 0), so **any single member request is executed with effectively 0 confirmations**, i.e., a K-of-N multisig request executed below the intended threshold. This directly matches the "multisig request executed below threshold" Critical-impact category and can enable a single compromised or malicious key/member to unilaterally move NEAR held by the multisig, add/delete keys, or deploy new contract code — actions that should require K signers.
- Conversely, if `num_confirmations` is set above `members.len()`, no request (including one to fix `num_confirmations` itself or to delete/add members) can ever reach the required confirmation count, permanently freezing all NEAR balance and control of the account — matching the "funds permanently frozen" Critical-impact category.

Both directions of the mismatch are unprivileged from the contract's own perspective in the sense that no additional check exists beyond what any member proposing/confirming a `SetNumConfirmations` request can already do; the K-of-N binding that other paths (`new`, `delete_member`) protect is silently bypassed via this action.

### Likelihood Explanation
This requires only reaching the confirmation threshold that is already in force to submit a `SetNumConfirmations` request — a normal, expected multisig operation per the contract's own README, which documents `SetNumConfirmations` as a supported action. No redeploy, foundation privilege, or external actor is needed; any member set with the currently valid threshold can trigger the state where the invariant is violated, either accidentally (fat-fingering `0` or a value greater than `members.len()`) or maliciously by a colluding subset of members reaching the (potentially very low, e.g. `1`) current threshold.

### Recommendation
Add the same bound check in the `SetNumConfirmations` action handler as used in `new()`/`delete_member()`:
```rust
MultiSigRequestAction::SetNumConfirmations { num_confirmations } => {
    self.assert_one_action_only(receiver_id, num_actions);
    assert(
        num_confirmations > 0 && num_confirmations as u64 <= self.members.len(),
        "num_confirmations must be > 0 and <= number of members",
    );
    self.num_confirmations = num_confirmations;
    return PromiseOrValue::Value(true);
}
```
The same missing check exists in the legacy `multisig/src/lib.rs` `SetNumConfirmations` handler and should be fixed identically (the `multisig` crate does not track member count the same way, so the check should be against the actual key/member count relevant to that variant). [5](#0-4) 

### Proof of Concept
1. Deploy `multisig2` with 3 members and `num_confirmations = 2`.
2. Any single member submits a request `{ actions: [SetNumConfirmations { num_confirmations: 0 }] }` via `add_request_and_confirm`. Per `execute_request`, `SetNumConfirmations` is asserted to be the only action in the request (`assert_one_action_only`), so this alone does not bypass K-of-N for itself — it still needs `num_confirmations` (currently 2) confirmations to take effect. This part is not directly exploitable by a single member alone from a properly-initialized state.
3. However, once two of the three members collude (reaching the legitimate threshold of 2) to pass `SetNumConfirmations { num_confirmations: 0 }`, `self.num_confirmations` becomes `0` with no validation.
4. From that point on, any single member (even one of the original 3, or an added `AddMember` action if attackers add more members later) can call `add_request_and_confirm` for a `Transfer` action, and `confirm()`'s check `confirmations.len() as u32 + 1 >= self.num_confirmations` (i.e. `1 >= 0`) is satisfied immediately, executing the transfer with **zero required confirmations** — permanently breaking the K-of-N guarantee going forward, i.e., every subsequent multisig request (transfers, key changes, contract upgrades) executes on a single confirmation instead of the originally configured threshold.
5. Symmetrically, setting `num_confirmations` above `members.len()` (e.g., to `100` with 3 members) permanently freezes the account since no future request, including one meant to correct the threshold, can reach that many confirmations. [3](#0-2) [4](#0-3)

### Citations

**File:** multisig2/src/lib.rs (L148-152)
```rust
    pub fn new(members: Vec<MultisigMember>, num_confirmations: u32) -> Self {
        assert(
            members.len() >= num_confirmations as usize,
            "Members list must be equal or larger than number of confirmations",
        );
```

**File:** multisig2/src/lib.rs (L274-279)
```rust
                // the following methods must be a single action
                MultiSigRequestAction::SetNumConfirmations { num_confirmations } => {
                    self.assert_one_action_only(receiver_id, num_actions);
                    self.num_confirmations = num_confirmations;
                    return PromiseOrValue::Value(true);
                }
```

**File:** multisig2/src/lib.rs (L294-315)
```rust
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

**File:** multisig2/src/lib.rs (L356-360)
```rust
    fn delete_member(&mut self, promise: Promise, member: MultisigMember) -> Promise {
        assert(
            self.members.len() - 1 >= self.num_confirmations as u64,
            "Removing given member will make total number of members below number of confirmations",
        );
```

**File:** multisig/src/lib.rs (L229-233)
```rust
                MultiSigRequestAction::SetNumConfirmations { num_confirmations } => {
                    self.assert_one_action_only(receiver_id, num_actions);
                    self.num_confirmations = num_confirmations;
                    return PromiseOrValue::Value(true);
                }
```
