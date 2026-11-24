# Home Assistant

The alerts are one automation, shipped as a blueprint: [`homeassistant/truenas-backup.yaml`](../homeassistant/truenas-backup.yaml). It needs Home Assistant **2026.11** or later and the Companion app on your phone.

It does three things:

- **Failures, warnings and tests** from the container are pushed to the phone you pick, as time-sensitive notifications.
- **Successes** are recorded in a helper, and pushed too if you turn that on.
- **Missed runs**: on the days and time you choose (Mondays at 8:00 AM by default), it alerts you if no success was recorded in the last 12 hours. That catches what the container can't report itself: the primary down, the app stopped, the container crashed.

## Set it up

1. **Import the blueprint.** The quickest way is this button, which opens your Home Assistant with the GitHub copy filled in:

   [![Open your Home Assistant instance and show the blueprint import dialog with this blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fsoullink9%2FDocker-TrueNAS_Backup%2Fblob%2Fmain%2Fhomeassistant%2Ftruenas-backup.yaml)

   Or go to Settings > Automations & scenes > Blueprints > **Import blueprint** and paste a URL. From GitHub (the default):

   ```
   https://github.com/soullink9/Docker-TrueNAS_Backup/blob/main/homeassistant/truenas-backup.yaml
   ```

   Or from your Gitea, if Home Assistant can read it without logging in:

   ```
   https://<your-gitea-host>/soullink9/Docker-TrueNAS_Backup/raw/branch/main/homeassistant/truenas-backup.yaml
   ```

   Neither reachable? Copy the file to `/config/blueprints/automation/truenas-backup/truenas-backup.yaml` and reload blueprints.
2. **Create the helper** (for the missed-run check): Settings > Devices & services > Helpers > Create helper > Date and/or time > **Date and time**, named `TrueNAS backup last success`.
3. **Create the automation** from the blueprint (Blueprints > TrueNAS weekly backup alerts > Create automation) and fill in:

   | Field | What to put |
   |-------|-------------|
   | Webhook ID | A long random ID, e.g. `truenas-backup-` plus 20 random characters. Treat it like a password. |
   | Phone to notify | Your phone (any Companion app device) |
   | Also notify on success | Off for failures only, on for every run |
   | Last success helper | The helper from step 2. Empty turns the missed-run check off. |
   | Check on / Check at | The days `BACKUP_SCHEDULE` runs, and a time by which it should be done |
   | Counts as missed after | How old the last success may be at check time. Default 12 h. |

4. **Point the container at it**: set `HA_WEBHOOK_URL` in `.env` to `http://<home-assistant-ip>:8123/api/webhook/<your-webhook-id>`, recreate the container, and run:

   ```
   docker exec truenas-backup backup --test-notify
   ```

   A "TrueNAS backup test" notification should arrive.

The webhook only accepts requests from your local network (`local_only`); the primary is on the same network.

## Updating the blueprint

Home Assistant remembers the URL it was imported from (the file itself also names the GitHub copy as its source). After a new release, open Settings > Automations & scenes > Blueprints, use the blueprint's menu > **Re-import blueprint**, and the automation picks up the change. Your field values stay as they are.

## What the container sends

```json
{"status": "failure", "title": "TrueNAS backup failed", "message": "Replication failed: ...", "source": "<container host>", "target": "backup TrueNAS"}
```

`status` is `success`, `failure`, `warning` (replication worked but the box didn't shut down) or `test`.
