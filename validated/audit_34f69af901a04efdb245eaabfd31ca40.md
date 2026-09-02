### Title
Multisig `num_confirmations` threshold can be set to zero, letting a single member execute requests below the required confirmation count - ([File: multisig2/src/lib.rs])

### Summary
The external report describes a Basket contract whose `validateWeights` only checks that two array lengths match (`_tokens.length == _weights.length`) without checking that the length is non-zero, letting a Publisher create a degenerate "0 tokens / 0 weights" basket that requires no collateral for `mint`, breaking the collateral-vs-supply custody binding. The same class of bug exists in `multisig2/src/lib.rs`: the invariant meant to bind "confirmations required" to "live members" (`num_confirmations` : `members.len()`) is checked only as a relative inequality and never validated to be non-zero, either at initialization or when it is changed later via a request.

### Finding Description
On initialization, `MultiSigContract::new` only asserts: [1](#0-0) 
`members.len() >= num_confirmations as usize` is trivially satisfied when `num_confirmations == 0`, exactly like the Basket's `_tokens.length == _weights.length` being trivially satisfied at `0 == 0`. No lower bound of `num_confirmations > 0` exists.

The threshold is enforced only in `confirm`: [2](#0-1) 
The gate is `confirmations.len() as u32 + 1 >= self.num_confirmations`. If `num_confirmations == 0`, this is always true after a single confirmation (in fact even the very first confirming member satisfies `1 >= 0`), so the "K of N" security property collapses to "1 of N" — i.e., the confirmations actually counted (1) diverge from the confirmations that should be required to authorize movement of funds (K).

Worse, the threshold is not fixed at deployment; it can be changed at runtime via `SetNumConfirmations`, and this action performs no validation either: [3](#0-2) 
`self.num_confirmations = num_confirmations;` accepts any `u32`, including `0`, with no check against `self.members.len()` or against `0`. The `multisig-factory` contract also forwards an attacker-controlled `num_confirmations` straight into `new` without any check: [4](#0-3) 

The custody binding broken is: *confirmations required to authorize a `Transfer`/`FunctionCall`/`AddKey` request* should equal the documented "K of N" threshold, but with `num_confirmations = 0` that binding becomes *confirmations required = 0*, meaning any single member (not necessarily K members, and not necessarily a majority) can execute arbitrary requests, including `Transfer` of the account's full NEAR balance, `AddKey` (granting a full-access key), or `DeployContract` (arbitrary code takeover).

### Impact Explanation
This matches the Critical impact category "a multisig request executed below threshold." A multisig deployed (via `multisig-factory` or directly) with `num_confirmations = 0`, or one whose threshold is later reduced to `0` through a `SetNumConfirmations` request, no longer enforces the K-of-N guarantee that depositors/users rely on when trusting funds to the account. Once the threshold is `0`, a single member's `add_request_and_confirm` call immediately executes a `Transfer` moving out the full account balance — a request "executed" with zero real confirmations required, i.e., strictly below the documented/intended threshold.

### Likelihood Explanation
Reaching `num_confirmations = 0` requires either (a) the deployer/factory caller setting it directly at creation time — fully unprivileged and immediate, since `multisig-factory::create` performs no validation of the `num_confirmations` argument, or (b) an already-approved `SetNumConfirmations` request reducing it to `0`. Path (a) requires no privilege at all beyond deploying a new multisig instance (analogous to the Publisher creating the zero-weight Basket in the original report) and is directly reachable by any caller of the factory or the raw `new` constructor.

### Recommendation
Add an explicit lower-bound check wherever `num_confirmations` is set:
- In `MultiSigContract::new`, require `num_confirmations > 0` in addition to `members.len() >= num_confirmations`.
- In `execute_request`'s `SetNumConfirmations` branch, require `num_confirmations > 0 && num_confirmations as u64 <= self.members.len()` before assigning `self.num_confirmations`.
- Apply the equivalent fix to `multisig/src/lib.rs` (the v1 contract), whose `new`/`SetNumConfirmations` share the same pattern (partially confirmed; full v1 `new` body was not fully inspected due to truncation).

### Proof of Concept
1. Attacker calls `multisig-factory::create` with `members: [{"account_id": "attacker"}]`, `num_confirmations: 0` [5](#0-4)  — the deployed `multisig2` contract initializes with `num_confirmations = 0`, passing the check `members.len() (1) >= num_confirmations (0)` [1](#0-0) .
2. Once the account is funded (e.g., by depositors who believe a threshold is enforced, or by the attacker themselves for a later social-engineering deposit), attacker calls `add_request_and_confirm` with a `Transfer` request for the full balance.
3. Inside `confirm`, `confirmations.len() as u32 + 1 (=1) >= self.num_confirmations (0)` is true, so `execute_request` runs immediately with only 1 confirmation [6](#0-5) , transferring out the funds despite the "multisig" branding implying a higher threshold.

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

**File:** multisig-factory/src/lib.rs (L28-49)
```rust
    #[payable]
    pub fn create(
        &mut self,
        name: AccountId,
        members: Vec<MultisigMember>,
        num_confirmations: u64,
    ) -> Promise {
        let account_id = format!("{}.{}", name, env::current_account_id());
        Promise::new(account_id)
            .create_account()
            .deploy_contract(CODE.to_vec())
            .transfer(env::attached_deposit())
            .function_call(
                b"new".to_vec(),
                json!({ "members": members, "num_confirmations": num_confirmations })
                    .to_string()
                    .as_bytes()
                    .to_vec(),
                0,
                env::prepaid_gas() - CREATE_CALL_GAS,
            )
    }
```
