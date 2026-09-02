### Title
Multisig `confirm()` executes requests using stale confirmations from removed members, allowing execution below the live-member threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::confirm` in `multisig2/src/lib.rs` counts confirmations solely by the size of the `confirmations` `HashSet<String>` stored for a `request_id`, without verifying that each entry in that set still corresponds to a currently-live member. `delete_member` only purges outstanding *requests originated by* the removed member; it does not scan and strip that member's *confirmations* recorded on other still-pending requests. As a result, a request can reach and cross `num_confirmations` using a confirmation entry left behind by a member who has since been removed from the multisig, executing the request with fewer live-member approvals than the configured threshold.

### Finding Description
The core invariant of a K-of-N multisig is: `count(confirmations from currently-live members) >= num_confirmations` before a request executes. In `multisig2/src/lib.rs`, `confirm()` implements this as: [1](#0-0) 

It reads `confirmations.len()` directly with no cross-check against `self.members`. The `confirmations: LookupMap<RequestId, HashSet<String>>` map is populated by member identity strings when they call `confirm`: [2](#0-1) 

When a member is removed via `DeleteMember`, `delete_member` cleans up only the requests that member *originated* (`r.member == member`), and its own `num_requests_pk` entry — it never walks other pending requests' `confirmations` sets to strip that member's prior approvals: [3](#0-2) 

Concretely, with `num_confirmations = 3` and members `{A, B, C, D}`:
1. `A` creates request `R` via `add_request` (originating member recorded as `A`, `confirmations = {}`).
2. `B` calls `confirm(R)` → `confirmations = {B}`.
3. `C` calls `confirm(R)` → `confirmations = {B, C}` (2 < 3, not yet executed).
4. Separately, the multisig approves a `DeleteMember{C}` request and removes `C`. `delete_member` only removes requests where `r.member == C` (i.e., requests C *originated*), not `R` (originated by `A`), so `R`'s confirmations still contain `C`'s stale entry.
5. `D` calls `confirm(R)` → `confirmations.len() as u32 + 1 = 3 >= num_confirmations (3)` → `execute_request(R)` fires.

`R` executes with confirmations recorded from `{B, C, D}`, but `C` is no longer a member — only `B` and `D` are currently-live approvers, i.e. 2 live confirmations, one less than the required 3. The equality the contract is supposed to guarantee — `recorded confirmations == live-member confirmations` — is broken; the recorded count silently includes a party whose trust/authorization was revoked.

### Impact Explanation
This breaks the core custody/authorization binding of the multisig: a `Transfer`, `FunctionCall`, `AddKey`, or any other privileged `MultiSigRequestAction` (including funds transfers out of the account) can be executed with fewer live, currently-trusted approvals than `num_confirmations` requires. This directly matches the "Critical" impact category of "a multisig request executed below threshold," since NEAR (or any action gated by the K-of-N scheme) can move, or privileged account changes can be made, without the actual current quorum of trusted members having approved it.

### Likelihood Explanation
The scenario requires no attacker-controlled exploit beyond normal, expected multisig operations: request creation, partial confirmation, a legitimate member-removal request (e.g., replacing a compromised or departing signer), and then the removed member's stale confirmation silently counting toward a different, still-pending request. Any organization that periodically rotates or revokes multisig members while other requests are mid-flight (a routine operational pattern) is exposed. No redeploy, special key access beyond normal member actions, or social engineering is needed — it is a straightforward code-path gap in `delete_member`/`confirm`.

### Recommendation
When removing a member (`delete_member`), iterate over **all** pending requests' `confirmations` sets (not only requests originated by that member) and remove the departing member's entry from each. Alternatively, change `confirm()` to recompute the *live* confirmation count by filtering `confirmations` against `self.members` before comparing to `num_confirmations`, rather than trusting the raw `HashSet` length.

### Proof of Concept
Using the existing test harness in `multisig2/src/lib.rs` (`members()`, `context_with_key`/`context_with_account` helpers):
1. `let mut c = MultiSigContract::new(members(), 3);` with members `{alice, bob, key1, key2}`.
2. As `alice`, `add_request` a `Transfer` request `R` (unconfirmed by alice as originator).
3. As `bob`, `c.confirm(R)` → confirmations `{bob}`.
4. As `key1`, `c.confirm(R)` → confirmations `{bob, key1}` (2/3).
5. As some quorum, `add_request_and_confirm`/`confirm` a `DeleteMember{key1}` request targeting `alice` (the multisig account) — this succeeds and removes `key1` from `self.members`, but `R`'s `confirmations` set still contains `key1`.
6. As `key2`, `c.confirm(R)` → `confirmations.len() + 1 == 3 >= num_confirmations` → `execute_request(R)` runs, transferring funds even though only `bob` and `key2` are currently live confirmers (2 of the required 3).

This can be verified directly against the code shown in `delete_member` (only filters `self.requests` by `r.member == member`, never touching `self.confirmations` for requests originated by *other* members) at [4](#0-3)  combined with the unguarded length check in `confirm` at [5](#0-4) .

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
