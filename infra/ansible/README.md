# EtR declarative Raspberry configuration

This directory is the machine-state source of truth for the EtR Raspberry.

Principles:
- GitHub defines the expected state.
- `inventory.yml` targets only the local Raspberry runner `etr-core`.
- `group_vars/all.yml` records the physical/runtime contract observed on 2026-09-07.
- `site.yml` manages critical deployed files from the repository and verifies hardware/runtime invariants.
- Validation must run in `--check --diff` mode before any apply.
- Applying changes must be followed by real Raspberry proof; a green workflow alone is not final validation.

Current physical contract:
- host `etr-core`, ARM64
- user `oryx`
- X display `:1`
- physical SPI framebuffer `/dev/fb1`, `fb_ili9486`, 480x320, 16 bpp
- runner service `actions.runner.ORYX-WORLD-EtR-core.etr-core.service`
- runner binary root `/home/oryx/actions-runner/actions-runner`
- active kernel family `6.18.39+rpt-rpi-*`

Known drift at migration start:
- `/home/oryx/EtR-core` was behind GitHub (`68e63e68...`).
- two LXDE sessions were running simultaneously; the declared invariant is exactly one.
- legacy provisioning text still describes a virtual `:2` remote desktop even though current VNC is bound to the physical `:1` framebuffer. This must be reconciled before the legacy installer is considered authoritative.
