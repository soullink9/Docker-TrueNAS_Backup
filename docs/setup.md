# Setup

Everything runs on the **primary** TrueNAS (SCALE 25.04 or later). The backup box only needs a reachable BMC and the replication target it already has.

## 1. Prepare the backup box

1. **Give the BMC a fixed IP.** This is the IPMI address, not the TrueNAS address. Set it in the BMC web UI or the BIOS.
2. **Add a BMC user for the container**, such as `backupctl`, with **Operator** privilege (it can switch power but not change the BMC). Make sure "IPMI over LAN" is on.
3. **Test it from the primary** (System > Shell):

   ```
   ipmitool -I lanplus -H <BMC-IP> -U backupctl -P '<password>' chassis power status
   ```

   It should print `Chassis Power is off` or `on`. If it times out, check the BMC cable and that UDP port 623 is open between the boxes.
4. **Optional: Wake-on-LAN fallback.** WOL must be on in the BIOS. Linux can switch it off on the card, so on the backup box add System > Advanced Settings > Init/Shutdown Scripts: type Command, When Post Init, `ethtool -s <interface> wol g` (e.g. `eno1`). Note that port's MAC for `WOL_MAC`.
5. **Scrubs and SMART tests.** The box is only on for a few hours a week, so schedule them for a Monday time when it is on (e.g. 1:15 AM). A scrub cut short by the shutdown carries on at the next boot.

## 2. Change the replication task on the primary

The container starts the replication, so the task must stop running on its own schedule (it would fail whenever the backup box is off).

1. **Data Protection > Replication Tasks**, edit the task, switch to **Advanced**.
2. Under **Schedule**, clear **Run Automatically** and leave **Schedule** unchecked.
3. Turn on **Save Pending Snapshots**, so the primary keeps snapshots the backup box hasn't received yet.
4. Give the **periodic snapshot task** that feeds it a lifetime of at least **3 weeks**. If the newest snapshot on the backup box is gone from the primary, replication has to start over.
5. Note the task's exact **Name** for `REPLICATION_TASKS`.

## 3. Create an API key on the primary

The container uses the primary's own API to start the replication and follow its job.

1. In the primary's web UI: user menu (top right) > **API Keys** > **Add**. Name it `truenas-backup`, pick the admin user (it needs to run replication tasks and read jobs), and copy the key: it is shown once.
2. Put it in `TRUENAS_API_KEY`.

Keep `TRUENAS_URL` on **`wss://`**. TrueNAS revokes an API key that is sent over plain `ws://`. Its certificate is self-signed by default, which is why `TRUENAS_VERIFY_TLS` defaults to `false`; set it to `true` if the primary has a trusted certificate for the name in the URL. With host networking (below) the container reaches the primary at `wss://127.0.0.1/api/current`; if the web UI listens on another port, add it (`wss://127.0.0.1:8443/api/current`).

## 4. Add the app

On TrueNAS: **Apps > Discover Apps > Custom App > Install via YAML** (or any compose project). Full example: [`compose.example.yaml`](../compose.example.yaml).

```yaml
services:
  truenas-backup:
    image: <your-gitea-host>/<owner>/docker-truenas_backup:latest
    container_name: truenas-backup
    restart: unless-stopped
    network_mode: host
    env_file: /mnt/tank/apps/truenas-backup/.env
    environment:
      TZ: America/New_York
      BACKUP_SCHEDULE: "0 1 * * mon"
```

- **`network_mode: host`** is needed: Wake-on-LAN broadcasts must reach the LAN, and the API is reached at `127.0.0.1`.
- **`env_file`**: copy [`.env.example`](../.env.example) to a dataset (e.g. `/mnt/tank/apps/truenas-backup/.env`), fill it in, and make it readable by root only (`chmod 600`). It holds the IPMI password and the API key.
- **Pulling from Gitea**: if the package is private, add your Gitea registry with a token that has `read:package` under **Apps > Configuration > Manage Container Images / Docker Registries**, or run `docker login <your-gitea-host>` on the primary.

## 5. Settings

| Variable | Default | Notes |
|----------|---------|-------|
| `TZ` | UTC | Your time zone, e.g. `America/New_York`. The schedule uses it. |
| `BACKUP_SCHEDULE` | `0 1 * * mon` | When to run, cron syntax: minute hour day month weekday. `0 2 * * sat` = Saturdays at 2:00 AM; `0 1 * * mon,thu` = twice a week. |
| `RUN_ON_START` | `false` | `true` also runs once right after the container starts. |
| `BACKUP_HOST` | (required) | The backup box's TrueNAS IP, the one the replication's SSH connection uses. |
| `BACKUP_NAME` | `BACKUP_HOST` | Friendly name in notifications. |
| `CHECK_PORT` | `22` | The port that answers once the box has booted. |
| `IPMI_HOST`, `IPMI_USER`, `IPMI_PASSWORD` | | The BMC's IP and the Operator user. Needed to power off. |
| `IPMI_INTERFACE` | `lanplus` | ipmitool interface; `lan` only for very old BMCs. |
| `WOL_MAC`, `WOL_BROADCAST` | empty, `255.255.255.255` | Optional Wake-on-LAN fallback for power-on. Use your LAN's broadcast, e.g. `192.168.1.255`. |
| `TRUENAS_URL` | (required) | The primary's API, e.g. `wss://127.0.0.1/api/current`. |
| `TRUENAS_API_KEY` | (required) | From step 3. |
| `TRUENAS_VERIFY_TLS` | `false` | `true` only with a trusted certificate. |
| `REPLICATION_TASKS` | (required) | Task names, exactly as shown in TrueNAS, comma-separated. Run in order. |
| `REPLICATION_RETRIES` | `1` | Retries for a failed task. |
| `RETRY_WAIT` | `300` | Seconds between tries. |
| `REPLICATION_TIMEOUT` | `43200` | Seconds (12 h) before a task still running counts as failed. It is never stopped, and the box is never powered off under it. |
| `BOOT_TIMEOUT` | `900` | Seconds to wait for the box to answer after power-on. |
| `SETTLE_SECONDS` | `120` | Extra wait after it answers, so every TrueNAS service is up. |
| `SHUTDOWN_TIMEOUT` | `900` | Seconds to wait for the box to report power off. |
| `SHUTDOWN_ON_FAILURE` | `false` | `true` powers the box off even after a failed replication. `false` leaves it on so you can look. |
| `SHUTDOWN_IF_ALREADY_ON` | `false` | `true` powers the box off at the end even if it was already on when the run started (someone may be using it). |
| `HA_WEBHOOK_URL` | empty | The Home Assistant webhook. See [Home Assistant](home-assistant.md). |
| `HA_VERIFY_TLS` | `true` | `false` only for HTTPS with a self-signed certificate. |
| `LOG_LEVEL` | `INFO` | `DEBUG` for more detail. |

If a setting is missing or wrong, the log lists every problem at start, no backup is scheduled, and the container shows as **unhealthy**. Fix `.env` and recreate the container.

## 6. Test before the first scheduled run

With the backup box off:

```
docker exec truenas-backup backup --test-notify    # a test alert on your phone
docker exec truenas-backup backup --no-shutdown    # power on + replicate, box left on
docker logs -f truenas-backup                      # follow it (second shell)
docker exec truenas-backup backup                  # the full cycle, as the schedule runs it
```

Then check that the replication task in Data Protection shows FINISHED with today's time, and that the backup box shut down cleanly. To test an alert, set `REPLICATION_TASKS` to a name that doesn't exist, recreate the container, run `backup`, and put the real name back.

Only one run happens at a time: a manual run while a scheduled one is going (or the other way round) is skipped.

## Health and status

The container is **healthy** when the scheduler is running and the settings are valid. A failed backup does not make it unhealthy: that is what the Home Assistant alert is for. The next run time and the last result:

```
docker exec truenas-backup python /opt/truenas-backup/healthcheck.py --status
```

## When something goes wrong

| Alert says | What the container did | Check |
|------------|------------------------|-------|
| Could not power on | Stopped; the box stays off | BMC IP, user and password; the `ipmitool` test in step 1 |
| Did not come up on port 22 | Stopped; the box may be on | The BMC console: a stuck boot, a failed disk, a changed IP. Raise `BOOT_TIMEOUT` if it just boots slowly |
| Replication failed (FAILED: ...) | Retried, then left the box on | The error, and the task in Data Protection. Missing snapshots and SSH keys are the usual causes |
| login failed / cannot connect | Left the box on | `TRUENAS_API_KEY` (revoked if it was ever sent over `ws://`) and `TRUENAS_URL` |
| Still running after 12h | Left the box on and the replication running | Usually a large first sync. Power the box off after it finishes, or raise `REPLICATION_TIMEOUT` |
| No replication task named ... | Stopped | The name must match exactly |
| Box still on | Replication worked, but the box didn't power off | Whether TrueNAS ignores the power button; shut it down from its UI |
| Missed run (from Home Assistant) | Nothing reached Home Assistant | Whether the primary and the container were up, and `docker logs truenas-backup` |

## Updating

```
docker compose pull truenas-backup && docker compose up -d truenas-backup
```

On TrueNAS Apps, use the app's **Update** / **Pull image** action. To stay on one version, use a version tag (e.g. `:0.1.0`) instead of `:latest`. Each release lists its tag.
