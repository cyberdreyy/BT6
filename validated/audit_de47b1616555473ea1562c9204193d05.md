### No Vulnerability found for this question.

The target function `Archive` in `commands/helpers/archive/ziplegacy/zip_legacy_archiver.go` performs no enumeration at all — it simply receives an already-computed `files map[string]os.FileInfo`, sorts the keys, and hands them to `archives.CreateZipArchive`. [1](#0-0) 

The actual enumeration logic (including untracked-file handling) lives in `commands/helpers/file_archiver.go`, not in the cited archiver. `processUntracked` runs `git ls-files -o -z`, which is scoped to the git repository (the job's checkout/working directory), and each resulting path is passed through `process`, which rejects anything resolving outside the working directory (`c.wd`) via a relative-path `..` check. [2](#0-1) [3](#0-2) 

Because `git ls-files -o` is bounded to the git working tree and `process`/`findRelativePathInProject` reject paths outside `c.wd`, untracked-file enumeration cannot reach runner-adjacent files or another job's workspace — it only picks up files already present in the current job's own checkout, which is the documented behavior of `artifacts:untracked`. There is no missing check here that lets an attacker escape the build directory boundary, and the cited `Archive` function itself contains no enumeration logic to exploit. [4](#0-3)

### Citations

**File:** commands/helpers/archive/ziplegacy/zip_legacy_archiver.go (L34-43)
```go
// Archive archives all files as new gzip streams.
func (a *archiver) Archive(ctx context.Context, files map[string]os.FileInfo) error {
	sorted := make([]string, 0, len(files))
	for filename := range files {
		sorted = append(sorted, filename)
	}
	sort.Strings(sorted)

	return archives.CreateZipArchive(a.w, sorted)
}
```

**File:** commands/helpers/file_archiver.go (L65-88)
```go
func (c *fileArchiver) process(match string) bool {
	var absolute, relative string
	var err error

	absolute, err = filepath.Abs(match)
	if err == nil {
		// Let's try to find a real relative path to an absolute from working directory
		relative, err = filepath.Rel(c.wd, absolute)
	}

	if err == nil {
		// Process path only if it lives in our build directory
		if !strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
			excluded, rule := c.isExcluded(relative)
			if excluded {
				c.exclude(rule)
				return false
			}

			err = c.add(relative)
		} else {
			err = errors.New("not supported: outside build directory")
		}
	}
```

**File:** commands/helpers/file_archiver.go (L224-262)
```go
func (c *fileArchiver) processUntracked() {
	if !c.Untracked {
		return
	}

	found := 0

	var output bytes.Buffer
	cmd := exec.Command("git", "ls-files", "-o", "-z")
	cmd.Env = os.Environ()
	cmd.Stdout = &output
	cmd.Stderr = os.Stderr
	logrus.Debugln("Executing command:", strings.Join(cmd.Args, " "))
	err := cmd.Run()
	if err != nil {
		logrus.Warningf("untracked: %v", err)
		return
	}

	reader := bufio.NewReader(&output)
	for {
		line, err := reader.ReadString(0)
		if err == io.EOF {
			break
		} else if err != nil {
			logrus.Warningln(err)
			break
		}
		if c.process(line[:len(line)-1]) {
			found++
		}
	}

	if found == 0 {
		logrus.Warningf("untracked: no files")
	} else {
		logrus.Infof("untracked: found %d files", found)
	}
}
```

**File:** commands/helpers/file_archiver.go (L264-282)
```go
func (c *fileArchiver) enumerate() error {
	wd, err := os.Getwd()
	if err != nil {
		return fmt.Errorf("failed to get current working directory: %w", err)
	}

	c.wd = wd
	c.files = make(map[string]os.FileInfo)
	c.excluded = make(map[string]int64)

	c.processPaths()
	c.processUntracked()

	for path, count := range c.excluded {
		logrus.Infof("%s: excluded %d files", path, count)
	}

	return nil
}
```
