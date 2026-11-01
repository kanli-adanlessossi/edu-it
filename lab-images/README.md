# Lab images

Sandbox images for hands-on labs. Requirements 4.1 to 4.4 and 21.5.

| Image | Path | Contents |
|---|---|---|
| `edu-it/lab-linux-base` | `linux-base/` | Alpine 3.21 plus `bash`, `coreutils`, `findutils`, `tar`, `procps` |
| `edu-it/lab-python` | `python/` | The base image plus `python3` |

## Rules

- The only base image is `alpine@sha256:<digest>` in `linux-base/Dockerfile`, pinned by the
  multi-arch index digest. The Python image builds on the base through the bake named context
  `linux-base` (see `docker-bake.hcl`), so it has no digest of its own.
- Users: `learner` (uid 1000) and `grader` (uid 1001). Group `devs` (gid 2000) is baked in with
  `learner` as a member, because the sandbox root filesystem is read-only at run time.
- No setuid or setgid files (stripped at build time).
- Default user is `learner` (`USER learner`).
- No secrets, no Check_Bundle, no solution files. Those are copied in at run time only.
- `python3` is installed only in the Python image.

## Catalog image name to Docker reference

Lab content (`labs.json`, field `image`) names an image by its directory here: `linux-base` or
`python`. The Docker provider maps that name to `edu-it/lab-<image>:<tag>` in
`edu_it_infrastructure.sandbox.docker_spec.resolve_image`, which is the reference `docker-bake.hcl`
tags. The tag is `dev` unless `LAB_IMAGE_TAG` is set (`CompositionSettings.lab_image_tag`, passed to
`DockerSandboxProvider(image_tag=...)`). A value that is already a Docker reference (it contains `/`,
`:` or `@`, for example a digest-pinned image) is used unchanged. Other providers use the name as is.

## Build

```sh
docker buildx bake --load
```

Bumping the digest: see `docs/LABS.md`.

### Alpine mirror override (local builds only)

Packages come from `https://dl-cdn.alpinelinux.org/alpine` by default, which is what CI uses. If the CDN is
unreachable from your machine, override the build-time mirror (main and community of v3.21):

```sh
ALPINE_MIRROR=https://uk.alpinelinux.org/alpine docker buildx bake --load
``` 

PowerShell: `$env:ALPINE_MIRROR='https://uk.alpinelinux.org/alpine'; docker buildx bake --load`. Another mirror that
works is `https://mirror.leaseweb.com/alpine`. The override is an `ARG` used only by the `apk add` step
(through a temporary `--repositories-file`), so the final image's `/etc/apk/repositories` still lists the CDN.
The base image digest pin is unchanged. Never commit a different default.

## Command forms used inside sandboxes

Recorded by the flag-compatibility spike (task 3.4, Requirements 4.15, 4.16). Verified in both built
images (`edu-it/lab-linux-base:dev` and `edu-it/lab-python:dev`, Alpine 3.21, identical results) in a
container started like a sandbox: `--init --network none --read-only --cap-drop ALL
--security-opt no-new-privileges --user 1000:1000`, with `docker exec -u learner|grader|root`.
Code must use only these absolute paths and argv forms.

| Command | Absolute path | Implementation | Version |
|---|---|---|---|
| `ps` | `/bin/ps` | procps-ng (not BusyBox) | 4.0.4 |
| `kill` | `/bin/kill` | BusyBox applet (symlink to `/bin/busybox`); `/usr/bin/kill` does not exist | n/a |
| `timeout` | `/usr/bin/timeout` | GNU coreutils (multicall `/usr/bin/coreutils`) | 9.5 |
| `tar` | `/bin/tar` | GNU tar | 1.35 |

`kill` is also a shell builtin, so always exec the absolute path `/bin/kill`, never the bare name.

### Argv forms that work

| Purpose | argv | Notes |
|---|---|---|
| Freeze | `["/bin/kill", "-s", "STOP", "-1"]` | rc 0. Stops every process the exec user may signal (learner: learner processes; grader: grader processes), except PID 1 and the kill itself. |
| Thaw | `["/bin/kill", "-s", "CONT", "-1"]` | rc 0. Same scope. |
| Kill all of a user's processes | `["/bin/kill", "-s", "KILL", "-1"]` | rc 0. Also accepted: `-9 -1`, `-KILL -1`. |
| Process listing, user and state | `["/bin/ps", "-eo", "user,stat,pid,comm"]` | Header line `USER STAT PID COMMAND`; `-e` shows all users. `["/bin/ps", "-e", "-o", "user=", "-o", "stat=", "-o", "pid=", "-o", "comm="]` is the header-less form (the `user=` and `stat=` pair was verified; `user:12` widths and `args` also work). `STAT` begins with `T` for stopped, `Z` for zombie. |
| Run with a deadline | `["/usr/bin/timeout", "-s", "KILL", "<s>", "<cmd>", ...]` | Exit code 137 on timeout (also for a backgrounded grandchild: the whole wrapped command ends). Otherwise the command's own exit code (`exit 7` gives 7). Fractional seconds (`1.5`) accepted. `-s 9` also works. Not `-s TERM`/`-k` for the hard deadline: they exit 124. |
| Stream out | `["/bin/tar", "-c", "-C", "<dir>", "-f", "-", "<name>"]` | Archive on stdout. |
| Stream in as learner | `["/bin/tar", "-x", "-C", "<dir>", "-f", "-"]` | Archive on stdin, as `learner`. `-f -` may be omitted (stdin is the default). |
| Stream in as root (for `/grader`) | `["/bin/tar", "-x", "--no-same-owner", "-C", "<dir>", "-f", "-"]` | **`--no-same-owner` is required** as root: the container has no `CAP_CHOWN`, so without it tar exits 2 ("Cannot change ownership to uid 1000"). Extracted files are then owned by root. |

Other GNU tar flags confirmed accepted when creating: `--numeric-owner`, `--one-file-system`,
`--no-recursion`, `--hard-dereference`, `--owner=0`, `--group=0`, `--mode=a+rX`; when extracting:
`--no-same-owner`, `--no-same-permissions`, `-o`.

### Tool paths used by the seed lab scripts

The seed lab 1 setup script and Check scripts call these absolute paths, with PATH not relied on.
`test_image_commands.py::test_seed_lab_tool_paths` asserts each one in both images, and
`tests/labs/test_seed_lab_tool_paths.py` fails when a script uses a path missing from this table.

| Path | Implementation | Used for |
|---|---|---|
| `/bin/stat` | GNU coreutils 9.5 (symlink to `../usr/bin/coreutils`) | `stat -c '%F'`, `'%G %a'`, `'%s'` without `-L` |
| `/usr/bin/id` | GNU coreutils | `id -nG` in setup |
| `/usr/bin/find` | GNU findutils 4.10 | `find -P ... -perm -0002 ! -type l -print -quit` |
| `/usr/bin/sha256sum` | GNU coreutils | planted file content check |
| `/usr/bin/printf` | GNU coreutils | setup |
| `/bin/mkdir`, `/bin/chmod` | GNU coreutils | setup |

**Deviation: `stat` is `/bin/stat`, not `/usr/bin/stat`.** The Alpine `coreutils` package installs
`stat` only under `/bin` (there is no `/usr/bin/stat`; a test asserts this so a base-image change is
noticed). The Check scripts therefore use `/bin/stat`, as their headers say, rather than adding a
symlink to the image: the rest of the table is `/usr/bin` because that is where Alpine puts those
tools, and an extra link would only move the deviation into the Dockerfile.

### Findings the code must honor

- **`kill -KILL -- -1` is NOT usable with `/bin/kill`.** BusyBox rejects `--` ("kill: invalid number
  '--'", rc 1) but still processes the later `-1` and signals everything, so the result is "error
  exit plus the kill happens". Use `["/bin/kill", "-s", "KILL", "-1"]`. It works only through bash's
  builtin (`["/bin/bash", "-c", "kill -KILL -- -1"]`, rc 0); BusyBox `sh` fails the same way.
- **With `--init`, `kill -s KILL -1` as the container user stops the container.** The sandbox's main
  process (`sleep 600` in the spike) is owned by the container user (`learner`, uid 1000), so a
  learner-run `kill -s KILL -1` kills it; `docker-init` (PID 1) then exits and the container stops.
  The same happens for `kill -KILL -1` and `kill -9 -1`. Without `--init`, the main process is PID 1,
  is immune to signals from inside its PID namespace, and the container survives. A `grader`-run
  `kill -s KILL -1` does not touch learner processes (different uid), so it cannot end the container.
  Provider design (task 2.5) must resolve this before relying on the post-timeout learner kill.
- **`ps` always lists PID 1** (`docker-init`, user `learner`, state `Ss`), which never stops. Any
  "all learner processes are stopped" test (`learner_all_stopped`) must ignore PID 1. The sandbox's
  main process is signalled by `kill -s STOP -1` and shows as `T`.
- `kill -s STOP -1` returns rc 0 whether or not other processes exist; stopped shells show `Ts`.
- `tar` run as `grader` cannot read `/grader` contents it does not own (rc 2, "Permission denied");
  `/grader` is `0700 root`, so copy in and out of it as root.
- Symlinks inside an archive are extracted verbatim (`evil -> /etc` is created in the target); path
  safety is the caller's responsibility (Requirement 28.x no-follow rules).
- **Freeze and thaw must be sent without `timeout_s`.** `kill -s STOP|CONT -1` signals every process
  the exec user may signal, which includes the provider's own `/usr/bin/timeout` wrapper (it runs as
  that user). With a `timeout_s` the exec stops itself and hangs until the backstop. GradeRun sends
  freeze and thaw with `timeout_s=None`; the freeze bound is enforced by its own clock.
- **No `CAP_CHOWN`, so `/grader/run` is created by the grader.** The `/grader` tmpfs is created
  `root:grader`, mode `0775` (`docker_spec`; the image directory's `0700` is hidden by the mount). After
  the verified freeze GradeRun runs `chmod 0775 /grader` (root), `mkdir -m 0700 /grader/run` (grader), then
  `chmod 0755 /grader` (root), all before any Check, so during Checks nothing in `/grader` is group- or
  world-writable. The learner is not in group `grader` and no world-writable mode is used. Teardown
  empties `/grader/run` as `grader` (root has no `CAP_DAC_OVERRIDE` and cannot enter it), then empties
  `/grader` as root. No image change is needed.