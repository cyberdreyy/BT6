### Title
`SetNumConfirmations` accepts any value with no bound against live member count, permanently bricking the multisig and freezing custodied NEAR - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::execute_request` in `multisig2/src/lib.rs` applies the `SetNumConfirmations` action by directly overwriting `self.num_confirmations` with no check against the current number of live members. Every other member-count-affecting path (`delete_member`) enforces the invariant `members.len() >= num_confirmations`, but `SetNumConfirmations` bypasses it entirely, so a single successfully-confirmed request can raise the threshold above the number of members that can ever confirm again, permanently freezing all NEAR held by the multisig account.

### Finding Description
The multisig is a keyless NEAR account whose only way to move funds or change its own configuration is via `confirm()` reaching `num_confirmations` confirmations, which then calls `execute_request` [1](#0-0) .

`delete_member` explicitly protects the invariant "confirmations required ≤ live members": [2](#0-1) 

But `SetNumConfirmations`, handled in the same `execute_request` dispatcher, performs no such check — it unconditionally assigns the attacker/proposer-supplied value: [3](#0-2) 

The only place `num_confirmations` is validated against membership is at contract initialization: [4](#0-3) 

That check is never re-applied when `num_confirmations` is changed afterward via governance. Once a proposal setting `num_confirmations` to a value greater than `self.members.len()` accumulates enough confirmations to execute (which is possible as long as it is proposed while the threshold is still reachable, e.g. `num_confirmations` set to `members.len()` exactly, or to any value ≤ current threshold but > future/actual live signer availability), the invariant `confirmations required ≤ live members` is violated. From that point on, `confirm()` can never accumulate enough confirmations for **any** future request — including a corrective `SetNumConfirmations` or `AddMember` request — because reaching the (now-too-high) threshold is itself gated by the same broken threshold. This is structurally identical to the `ArmadaGovernor` bug: a value snapshotted/set by one setter (`SetNumConfirmations`) is never reconciled against a separately-mutable, live invariant-defining state (`members.len()`), and the specific setter that could have re-validated it (`delete_member`) enforces the check while the actual dangerous setter does not.

### Impact Explanation
The multisig contract is documented to hold NEAR and act as the sole custodian/signer for an account (`multisig-factory` deploys it with an attached NEAR balance) [5](#0-4) . Because the contract is keyless-by-design apart from member-controlled confirmation, once `num_confirmations > members.len()` no request — `Transfer`, `AddMember`, `DeleteMember`, `SetNumConfirmations`, `DeployContract` (upgrade) — can ever reach threshold again. All NEAR held by the account becomes permanently unrecoverable on-chain, matching the "Critical: funds permanently frozen" impact bucket.

### Likelihood Explanation
No attacker outside the multisig's own members is required — this can be triggered by an honest majority of members approving a `SetNumConfirmations` proposal that is not obviously dangerous on its face (e.g., raising `num_confirmations` to match the current member count is a common "tighten security" action), and later becomes fatal once membership drops even slightly (a member is removed or a key is otherwise lost) without a corresponding re-lowering of `num_confirmations` — but more directly, an admin/proposer can simply request `num_confirmations` set higher than `members.len()` in one step and get it confirmed, since the code performs no client-side or contract-side rejection. This mirrors the report's framing: a "routinely-good governance action" silently crosses a hidden coupling between two independently-mutable state variables (`num_confirmations` and `members`).

### Recommendation
In `multisig2/src/lib.rs`, add the same invariant check used in `delete_member` to the `SetNumConfirmations` handler in `execute_request`:

```diff
 MultiSigRequestAction::SetNumConfirmations { num_confirmations } => {
     self.assert_one_action_only(receiver_id, num_actions);
+    assert(
+        self.members.len() >= num_confirmations as u64,
+        "num_confirmations must not exceed the number of members",
+    );
     self.num_confirmations = num_confirmations;
     return PromiseOrValue::Value(true);
 }
```

This makes the `members.len() >= num_confirmations` invariant enforced by every code path that can change either side of it (`add_member`/`delete_member`/`SetNumConfirmations`), closing the brick.

### Proof of Concept
Given a `multisig2` contract initialized with 3 members and `num_confirmations = 2` (valid per `new`'s check):
1. A member proposes `{"type": "SetNumConfirmations", "num_confirmations": 3}` via `add_request`.
2. The proposal reaches 2 confirmations (still achievable, since threshold is currently 2) and executes via `execute_request`, setting `self.num_confirmations = 3` with no check against `self.members.len()` [3](#0-2) .
3. Separately (or subsequently), one member is removed via a `DeleteMember` request that itself required only 3 confirmations and was confirmed while 3 members could still reach 3-of-3 — leaving `members.len() == 2 < num_confirmations == 3`. (Note: `delete_member`'s own guard `members.len() - 1 >= num_confirmations` at [6](#0-5)  only prevents removing a member if it would make membership fall below confirmations at the moment of removal in isolation — but `SetNumConfirmations` never protects against being set to `members.len()` exactly, after which a normal, otherwise-unrelated single-member departure via any account-level key removal outside the multisig's own bookkeeping, or a future `DeleteMember` proposed and confirmed before this fix under threshold==count edge cases, leaves the contract at threshold==count, i.e. requiring unanimous confirmation forever with zero margin — and any successful `SetNumConfirmations` request that sets the value strictly above `members.len()` at execution time, which is never checked, bricks it immediately.)
4. From this point, `confirm()` on any new request — including one to lower `num_confirmations` back down — can never reach `self.num_confirmations` confirmations because there are fewer live members than the threshold, per `current_member`/`confirm` logic [1](#0-0) . All NEAR held by the account is permanently frozen.

This can be directly and deterministically reproduced by calling `SetNumConfirmations` with `num_confirmations > members.len()` in a single step (no need to also remove a member): since `execute_request`'s `SetNumConfirmations` branch performs zero validation, confirming a request that sets `num_confirmations = members.len() + 1` immediately bricks the contract in one transaction, with no subsequent recovery path — confirmed by inspecting [3](#0-2)  against the equivalent, present guard in [6](#0-5) .

### Citations

**File:** multisig2/src/lib.rs (L147-152)
```rust
    #[init]
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

**File:** multisig2/src/lib.rs (L355-360)
```rust
    /// Delete member from the list. Removes access key if the member is key based.
    fn delete_member(&mut self, promise: Promise, member: MultisigMember) -> Promise {
        assert(
            self.members.len() - 1 >= self.num_confirmations as u64,
            "Removing given member will make total number of members below number of confirmations",
        );
```

**File:** multisig-factory/README.md (L26-30)
```markdown
Create a new multisig with the given parameters and attached amount (50N) passed to multisig contract:

```
near call $CONTRACT_ID create '{"name": "test", "members": [{"account_id": "illia"}, {"account_id": "testmewell.testnet"}, {"public_key": "ed25519:Eg2jtsiMrprn7zgKKUk79qM1hWhANsFyE6JSX4txLEuy"}], "num_confirmations": 1}'  --accountId $CONTRACT_ID --amount 50 --gas 100000000000000
```
```
