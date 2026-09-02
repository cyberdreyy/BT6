Confirmed. The `confirm` function only checks `confirmations.len() as u32 + 1 >= self.num_confirmations` [1](#0-0)  without filtering out confirmations from members that have since been deleted. The `delete_member` function only removes requests *created* by the deleted member and clears `num_requests_pk` for them, but never scans `self.confirmations` to strip that member's stale confirmation entries from other pending requests they had merely confirmed [2](#0-1) . This lets a stale confirmation from a removed member still count toward the threshold on a later `confirm` call by a distinct live member.

### Title
Stale confirmations from deleted multisig members are counted toward the confirmation threshold, allowing requests to execute below the live-member threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::confirm` checks only the size of the `confirmations` `HashSet` against `num_confirmations`; it never checks whether the members who confirmed are still current members. `delete_member` removes only requests *created* by the removed member, and clears `num_requests_pk`, but leaves that member's account/key string inside the `confirmations` sets of any *other* pending requests they had previously confirmed. `Thankgod67Ikhide/core-contracts--011:multisig2/src/lib.rs:294-315,356-379`.

### Finding Description
The intended binding is: `execute(request) ⇒ |{live members who confirmed}| ≥ num_confirmations`.

Sequence that breaks it:
1. Multisig has members `{A, B, C, D}` and `num_confirmations = 3`.
2. Member `B` creates request `R` (auto-confirms): `confirmations[R] = {B}`.
3. Member `A` also confirms `R` (still below threshold of 3): `confirmations[R] = {A, B}`.
4. A separate request to `DeleteMember{A}` reaches threshold and executes `delete_member`, which checks `self.members.len() - 1 >= self.num_confirmations` (4-1=3 ≥ 3, passes), removes `A` from `self.members`, removes requests *created* by `A` (none, since `A` didn't create `R`), and removes `A`'s `num_requests_pk` entry — but it does **not** touch `confirmations[R]`, which still contains `"A"`.
5. Members are now `{B, C, D}`, still `≥ num_confirmations (3)`, so no obvious inconsistency is flagged.
6. `C` calls `confirm(R)`. Code path: `confirmations.len() as u32 + 1 >= self.num_confirmations` → `2 + 1 >= 3` → true → `execute_request(R)` runs.

At execution, only `B` and `C` are live members who ever actually approved `R`; `A`'s vote is a phantom left over from before removal. The request executes with only 2 live-member confirmations against a nominal 3-of-4 threshold — the K-of-N guarantee is broken.

### Impact Explanation
This crosses the authorisation/threshold boundary explicitly listed as Critical impact: "a multisig request executed below threshold." An arbitrary-value `Transfer`, `AddKey`, `FunctionCall`, or `DeployContract` request can be pushed through with fewer genuine approvals than configured, enabling unauthorized fund transfers or contract control changes by a minority of live signers colluding with a stale vote from a since-removed member.

### Likelihood Explanation
This requires only ordinary multisig usage patterns (a member confirming a request they didn't create, followed later by that member's removal via the normal `DeleteMember` governance flow) — no privileged actor abuse, no redeploy, and no reliance on nearcore/SDK bugs. Any multisig that actively rotates membership (a normal, expected lifecycle event) is exposed.

### Recommendation
In `delete_member`, iterate all entries in `self.confirmations` (or maintain confirmations keyed differently) and remove the deleted member's entry from every request's confirmation set, not just requests they created. Alternatively, in `confirm`/`execute_request`, re-validate that all recorded confirmers in `confirmations` are still members of `self.members` before counting them toward `num_confirmations`.

### Proof of Concept
```rust
// members = {A, B, C, D}, num_confirmations = 3
let r = contract.add_request_and_confirm(request_r); // as B -> confirmations[r] = {B}
// as A:
contract.confirm(r); // confirmations[r] = {A, B}, len 2 < 3, pending

// separately, executes DeleteMember{A} via normal 3-of-4 confirm flow
// -> delete_member(A) removes A from members, but confirmations[r] still == {A, B}

// members now = {B, C, D}, num_confirmations still 3
// as C:
contract.confirm(r); 
// confirmations.len() (2, i.e. {A,B}) + 1 == 3 >= num_confirmations(3) -> executes request `r`
// despite only B and C being live members who ever confirmed it
``` [2](#0-1) [1](#0-0)

### Citations

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

**File:** multisig2/src/lib.rs (L356-379)
```rust
    fn delete_member(&mut self, promise: Promise, member: MultisigMember) -> Promise {
        assert(
            self.members.len() - 1 >= self.num_confirmations as u64,
            "Removing given member will make total number of members below number of confirmations",
        );
        // delete outstanding requests by public_key
        let request_ids: Vec<u32> = self
            .requests
            .iter()
            .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
            .collect();
        for request_id in request_ids {
            // remove confirmations for this request
            self.confirmations.remove(&request_id);
            self.requests.remove(&request_id);
        }
        // remove num_requests_pk entry for member
        self.num_requests_pk.remove(&member.to_string());
        self.members.remove(&member);
        match member {
            MultisigMember::AccessKey { public_key } => promise.delete_key(public_key.into()),
            MultisigMember::Account { account_id: _ } => promise,
        }
    }
```
