# Home Assistant

The container posts every result to a Home Assistant webhook as JSON:

```json
{"status": "failure", "title": "TrueNAS backup failed", "message": "Replication failed: ...", "source": "<container host>", "target": "backup TrueNAS"}
```

`status` is `success`, `failure`, `warning` (replication worked but the box didn't power off) or `test`. Everything is in [`homeassistant/truenas-backup.yaml`](../homeassistant/truenas-backup.yaml).

## Set it up

1. **Helper.** Settings > Devices & services > Helpers > Create helper > Date and/or time > "Date and time", named `TrueNAS backup last success` (entity `input_datetime.truenas_backup_last_success`).
2. **Webhook id.** Pick a long random one, e.g. `truenas-backup-` plus 20 random characters. Anyone on your LAN who knows it can trigger the automation, so treat it like a password.
3. **Automation 1, result.** Settings > Automations & scenes > Create automation > Edit in YAML, paste the first automation. Replace `truenas-backup-CHANGE-ME` with your webhook id and `notify.mobile_app_YOUR_PHONE` with your phone's notify action (Developer tools > Actions, search `mobile_app`). It records successes in the helper and pushes everything else to your phone.
4. **Automation 2, missed run.** A second automation from the commented block at the end of the file, same notify action. On Mondays at 8:00 AM it alerts you if no success arrived in the last 12 hours. That catches what the container can't report itself: the primary down, the app stopped, the container crashed. If you change `BACKUP_SCHEDULE`, move its day and time to match.
5. **Set `HA_WEBHOOK_URL`** in `.env` to `http://<home-assistant-ip>:8123/api/webhook/<your-webhook-id>`, recreate the container, and run `docker exec truenas-backup backup --test-notify`.

The webhook is `local_only`, so it only accepts requests from your LAN; the primary is on the same network.
