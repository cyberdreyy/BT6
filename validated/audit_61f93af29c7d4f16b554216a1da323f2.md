### Title
TOCTOU between artifact hashing (SLSA provenance generation) and archive creation allows attestation digest to not match uploaded artifact bytes - (File: commands/helpers/artifact_metadata.go)

### Summary
`ArtifactsUploaderCommand.Execute` calls `enumerate()` to build the `c.files` map, then (if metadata generation is enabled) calls `generateStatementToFile` → `generateSubjects` (`artifact_metadata.go:191-229`), which opens and SHA-256-hashes each artifact file from disk and writes the resulting digests into the SLSA provenance statement. Only afterward does `Run()` → `createBodyProvider()` (`artifacts_uploader.go:99-139`) invoke the archiver (`archive.Archive`, e.g. `tarzstd/tarzstd_archiver.go:46-127`, `fastzip`, etc.), which independently re-opens and re-reads the same files from disk to build the uploaded archive. There is no re-hash, no file locking, and no comparison between the two reads.

### Finding Description
The relevant code path is:
1. `fileArchiver.enumerate()` (`file_archiver.go:264-282`) walks the working directory and records `os.FileInfo` for each matched artifact path into `c.files`, based on job-controlled `--path`/`--exclude`/`--untracked` inputs (attacker/job author fully controls which files and directory trees are declared as artifacts).
2. `ArtifactsUploaderCommand.Execute` (`artifacts_uploader.go:213-243`) calls `generateStatementToFile`, which calls `generateSubjects(opts.files)` (`artifact_metadata.go:191-229`). This opens each file with `os.Open`, streams it through SHA-256, and records the digest as an in-toto `ResourceDescriptor.Digest["sha256"]` for that artifact name/path.
3. The resulting statement (with the digests) is written to a metadata JSON file via `os.WriteFile` and added to `c.files` via `c.process(metadataFile)` (`artifacts_uploader.go:236`).
4. Later, `Run()` calls `createBodyProvider()` (`artifacts_uploader.go:99-139`), which spins up an `archive.NewArchiver` and calls `archiver.Archive(ctx, c.files)` in a goroutine. This re-opens every file path from `c.files` fresh from disk (see `tarzstd_archiver.go:110-119`, `fastzip` via `fa.Archive`) and streams its *current* bytes into the uploaded artifact archive.

Between step 2 and step 4 there is an unguarded window during which the underlying file on disk can be modified. In shell/docker/kubernetes executors, the build/artifacts directory is writable by any process the job spawned; a detached/background process started by the job's `script` (e.g. `nohup`, `setsid`, or a lingering container/service) can survive past the main script's completion and continue running while the runner's `artifacts-uploader` helper process (still on the same filesystem/volume) performs enumeration, hashing, and later archiving. There is no file locking, immutability check, content-addressed re-verification, or hash recomputation at archive time — `Archive` implementations only check path-containment (e.g. the `tarzstd` chroot check at `tarzstd_archiver.go:72-74`), not content integrity.

Consequently, an attacker (the pipeline/job author) can arrange for a declared artifact file to contain benign content `A` at hash-time and different content `B` at archive-time, producing a signed/attested statement whose `Subject[].Digest["sha256"]` records `hash(A)` while the actually uploaded artifact bytes are `B`. This breaks the core invariant that the attestation digest must correspond to the uploaded artifact bytes, undermining SLSA provenance guarantees for any downstream consumer that trusts the runner-generated attestation without independently re-verifying the digest against the delivered artifact bytes.

### Impact Explanation
This is a genuine provenance-integrity bug, but its practical exploitation value is limited: the pipeline author already fully controls both the artifact content and what gets attested, since it is their own job's build output. A downstream consumer that verifies the provenance digest against the actually downloaded artifact (the "correct" way to use SLSA attestations) would detect a mismatch and simply reject the artifact — i.e., the defense (digest verification) still catches the tampering, it just wasn't caught by GitLab Runner internally at upload time. The concrete negative impact is: (a) attestation/provenance data recorded in GitLab job artifacts is unreliable/non-atomic with respect to what was actually archived, and (b) any verifier that is lenient (or has a race of its own, or doesn't fully verify digests) could be misled. There is no cross-project/cross-job boundary violation, no privilege escalation, and no secret leakage — the impact is confined to the integrity of the job's own self-reported provenance metadata.

### Likelihood Explanation
Feasibility is moderate: requires the attacker's own job to spawn a background/detached process capable of running (with a filesystem write) during the (short) window between hashing and archiving, which is plausible with `nohup ... &` or a lingering sidecar/service container sharing the build volume, but the window is generally small (bounded by archiver startup, sequential enumeration/hashing time). It is fully reproducible/deterministic given control over timing (e.g., a background loop polling and swapping file content immediately after the job's main script finishes could reliably win the race, or one could deliberately delay the archive step, e.g. via a large number of artifact files, to widen the window).

### Recommendation
Compute the subject digests from the exact bytes that are archived rather than from a separate disk read. Concretely: either (1) archive first, then compute SHA-256 digests of the artifact file contents as they are streamed into the archive (e.g., wrap the archiver's per-file reader with a hashing `io.TeeReader` and populate `generateSubjects`'s digests from that pass), or (2) hash and archive in a single filesystem pass by having `generateSubjects` and `archive.Archive` share the same open file handle/read, so digest and packed bytes are guaranteed identical, eliminating the TOCTOU window entirely.

### Proof of Concept
Go integration test plan (extending `artifacts_uploader_test.go` / `artifact_metadata_test.go`):
```go
func TestArtifactMetadataTOCTOU(t *testing.T) {
    dir := t.TempDir()
    artifactPath := filepath.Join(dir, "artifact.txt")
    require.NoError(t, os.WriteFile(artifactPath, []byte("benign-content"), 0644))

    cmd := &ArtifactsUploaderCommand{ /* configure Paths: []string{"artifact.txt"}, wd: dir, GenerateArtifactsMetadata: true, ... */ }
    require.NoError(t, cmd.enumerate())

    // Simulate attacker background process: swap file content right after hashing
    origGenerateSubjects := cmd.generateSubjects
    // (or hook via test-only variant) — after generateStatementToFile computes digest, mutate file:
    metadataFile, err := cmd.generateStatementToFile(generateStatementOptions{
        artifactName: cmd.Name, files: cmd.files, artifactsWd: cmd.wd, jobID: cmd.ID,
    })
    require.NoError(t, err)

    // TOCTOU window: attacker mutates the artifact after hash, before archive
    require.NoError(t, os.WriteFile(artifactPath, []byte("malicious-content"), 0644))

    // Now build the archive as createBodyProvider/Run would
    _, provider := cmd.createBodyProvider()
    body, _ := provider.ReaderFactory()
    archived, _ := io.ReadAll(body)

    // Parse metadataFile statement, extract recorded sha256 digest for artifact.txt
    recordedDigest := extractDigestFromStatement(t, metadataFile, "artifact.txt")

    // Extract actual archived artifact.txt bytes and compute its digest
    actualDigest := sha256Of(extractFileFromArchive(t, archived, "artifact.txt"))

    // Assert the mismatch is NOT caught by the runner (proving the bug)
    assert.NotEqual(t, recordedDigest, actualDigest,
        "provenance digest does not match actually uploaded artifact bytes")
}
```
Expected result: `recordedDigest` (computed from `"benign-content"`) differs from `actualDigest` (computed from `"malicious-content"`), and no error/warning is raised by the runner during upload — demonstrating the attestation-artifact mismatch is silently produced and shipped.