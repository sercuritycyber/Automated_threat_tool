# Deploying the Threatintel pipeline to a VM

Two ways. Both run `bin/run.sh` on a 4-hour timer with linger enabled, so it
keeps running with nobody logged in — the reason to move off a laptop.

## A. Native (used on 192.168.0.112 now — 5 GB disk, no room for images)

    ssh <vm>
    git clone git@github-threatintel:sercuritycyber/Automated_threat_tool.git ~/Threatintel
    cd ~/Threatintel && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
    # copy config/env (chmod 600) and config/settings.yaml from a trusted host
    # unit files: ~/.config/systemd/user/cti-harvester.{service,timer} + cti-panel.service
    #   the service PATH must include  ~/Threatintel/.venv/bin  and  ~/.local/bin  (for claude)
    systemctl --user daemon-reload
    systemctl --user enable --now cti-harvester.timer cti-panel.service
    sudo loginctl enable-linger $USER

## B. Container (podman Quadlet — once the disk is >= 20 GB)

    mkdir -p ~/.config/containers/systemd
    cp deploy/threatintel.container deploy/threatintel.timer ~/.config/containers/systemd/
    podman build -t threatintel:latest -f deploy/Containerfile ~/Threatintel
    systemctl --user daemon-reload
    systemctl --user start threatintel.timer
    sudo loginctl enable-linger $USER

`config/`, `state/`, `logs/`, `drafts/` and the host's `~/.claude` are
bind-mounted, so secrets stay out of the image and state survives a rebuild.

## Health

    systemctl --user list-timers cti-harvester.timer
    journalctl --user -u cti-harvester.service -n 50 --no-pager
    curl -s localhost:8799/service        # dashboard: CTI service page
