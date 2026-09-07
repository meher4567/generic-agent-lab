# Prove reproduction on a second Ubuntu host

A same-machine clone validates packaging; a second host run validates the target
environment. Do not treat those as equivalent evidence.

Full validation has already passed on two separate fresh Ubuntu hosted runner instances
at the same commit; see the [validation record](validation-status.md). Use the steps
below to establish the result on your own target machines.

On a separate Ubuntu 24.04 x86_64 machine with the documented resources:

```bash
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/meher4567/generic-agent-lab.git
cd generic-agent-lab
git rev-parse HEAD
./scripts/start.sh
sudo agentlab report
```

Record the exact Git revision and retain the generated report from each host. Require
both reports to show `status: PASS`, `environment_ready: true`, three guest smoke results,
verified cleanup, and no skipped acceptance checks. The report's
`second_host_reproduction` field remains `NOT_VERIFIED_BY_THIS_RUN`: one machine cannot
assert that another machine was tested.

Use the same revision on both hosts. Save the host OS, Podman/libvirt versions, cloud
base digest, image IDs, and any required host-specific preparation from the reports.
This establishes what was actually reproduced even if Ubuntu updates differ over time.

The public repository excludes the original local planning input, runtime state, private
keys, VM images, logs, build artifacts, `.env` files, and reports. `scripts/check-public.py`
checks the staged/tracked content before publication. A fresh clone should contain only
generic implementation, test fixtures, documentation, and dependency locks.
