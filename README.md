# Decky Dialogs

Decky Dialogs is a Decky Loader plugin that lets local or SSH-invoked scripts display Steam Gaming Mode toasts and interactive dialogs.

It accepts one newline-terminated JSON request per Unix-socket connection.

## Naming

| Item | Value |
|---|---|
| Plugin name | `Decky Dialogs` |
| Source folder | `decky-dialogs` |
| Installed folder | `/home/deck/homebrew/plugins/DeckyDialogs/` |
| CLI command | `decky-dialogs` |
| Unix socket | `/tmp/decky-dialogs.sock` |
| Package name | `decky-dialogs` |

Decky-required entry-point names remain `main.py`, `src/index.tsx`, and `dist/index.js`.

## Requirements

- Decky Loader
- Python 3
- Node.js and pnpm for building on the Steam Deck
- The plugin must be enabled in Decky for the socket to exist

## Quick usage

### Toast with a title and body

```bash
decky-dialogs "Backup complete" "All files were copied."
```

### Toast with only a body

```bash
decky-dialogs "All files were copied."
```

The default title is `Decky Dialogs`.

### Toast with a duration

```bash
decky-dialogs --timeout 8 "Backup complete" "All files were copied."
```

### Dialog with buttons

```bash
decky-dialogs prompt \
  --id "restart-service" \
  --title "Restart service?" \
  --text "The configuration changed. Restart now?" \
  --button "restart=Restart" \
  --button "cancel=Cancel" \
  --timeout 30
```

Selected result:

```json
{"id":"restart-service","status":"selected","button":"restart"}
```

### Dialog with text input

```bash
decky-dialogs prompt \
  --title "Backup name" \
  --text "Enter a name for the backup." \
  --input \
  --input-label "Name" \
  --placeholder "Backup name" \
  --default-value "nightly" \
  --button "submit=Submit" \
  --button "cancel=Cancel" \
  --timeout 60
```

Example result:

```json
{"id":"5ec679cd-7d84-43cd-b687-72e32975c40c","status":"selected","button":"submit","input":"nightly"}
```

## CLI behavior

### Toast syntax

```text
decky-dialogs [--timeout SECONDS] [TITLE] MESSAGE
```

A toast prints nothing when successful and exits with code `0`.

### Dialog syntax

```text
decky-dialogs prompt [OPTIONS]
```

Available options:

| Option | Meaning |
|---|---|
| `--id ID` | Optional caller-defined request ID |
| `--title TEXT` | Dialog title |
| `--text TEXT` / `--body TEXT` | Dialog body; may contain newlines |
| `--button ID=LABEL` | Repeatable button definition |
| `--timeout SECONDS` | Timeout from 1 to 3600 seconds |
| `--input` | Enable text input |
| `--input-label TEXT` | Input field label |
| `--placeholder TEXT` | Additional input hint used by the protocol |
| `--default-value TEXT` | Initial input value |

If no button is supplied, an `OK` button is created.

### Dialog exit codes

| Code | Meaning |
|---:|---|
| `0` | A button was selected |
| `2` | Protocol, connection, or server error |
| `3` | Dialog timed out |
| `4` | Dialog was dismissed |

Always parse the JSON response instead of relying only on the exit code when the selected button or input value matters.

## Socket protocol

Socket path:

```text
/tmp/decky-dialogs.sock
```

Rules:

1. Open an `AF_UNIX`, `SOCK_STREAM` connection.
2. Send exactly one UTF-8 JSON value followed by `\n`.
3. Shut down the write side or leave the connection open.
4. Read one newline-terminated response.
5. Use a client timeout longer than the requested dialog timeout.

The maximum request size is 64 KiB.

### Toast request

Legacy object form:

```json
{"title":"Backup complete","body":"All files were copied."}
```

Explicit form:

```json
{"type":"notify","title":"Backup complete","body":"All files were copied.","timeout":8}
```

A JSON string is also accepted and uses `Decky Dialogs` as its title:

```json
"All files were copied."
```

Successful toast response:

```text
OK
```

### Dialog request

```json
{
  "type": "prompt",
  "id": "restart-service",
  "title": "Restart service?",
  "body": "The configuration changed. Restart now?",
  "buttons": [
    {"id": "restart", "label": "Restart"},
    {"id": "cancel", "label": "Cancel"}
  ],
  "timeout": 30,
  "input": null
}
```

`text` and `message` are accepted as aliases for `body`. The `id` is optional; the backend generates a UUID if it is omitted.

Input configuration:

```json
{
  "label": "Name",
  "placeholder": "Backup name",
  "default": "nightly"
}
```

The `input` property may be `null`, `false`, `true`, or an object. `true` enables input with defaults.

### Dialog responses

Button selected:

```json
{"id":"restart-service","status":"selected","button":"restart"}
```

Button selected with input:

```json
{"id":"restart-service","status":"selected","button":"restart","input":"nightly"}
```

Dismissed:

```json
{"id":"restart-service","status":"dismissed"}
```

Timed out:

```json
{"id":"restart-service","status":"timeout"}
```

Error:

```json
{"id":"restart-service","status":"error","message":"Description"}
```

## Guidance for LLMs and automation agents

When generating a Decky Dialogs command:

1. Use the executable name `decky-dialogs`, not the former `deck-notify` name.
2. Use a toast for informational messages that need no response.
3. Use `decky-dialogs prompt` when a decision or text answer is required.
4. Give every button a short, stable machine-readable ID and a human-readable label.
5. Prefer explicit IDs such as `approve`, `reject`, `retry`, or `cancel`.
6. Set a realistic timeout; the default is 30 seconds.
7. Quote all shell values safely. Do not interpolate untrusted text into shell commands.
8. Parse the returned JSON and branch on `status` before reading `button` or `input`.
9. Treat `timeout`, `dismissed`, and `error` as separate outcomes.
10. Remember that the plugin must be enabled in Decky before using the socket.

Recommended decision command:

```bash
result="$(decky-dialogs prompt \
  --id "deploy-confirmation" \
  --title "Deploy update?" \
  --text "Version 2.0.0 is ready." \
  --button "deploy=Deploy" \
  --button "cancel=Cancel" \
  --timeout 45)"
printf '%s\n' "$result"
```

Recommended Python client without the CLI helper:

```python
import json
import socket

request = {
    "type": "prompt",
    "id": "deploy-confirmation",
    "title": "Deploy update?",
    "body": "Version 2.0.0 is ready.",
    "buttons": [
        {"id": "deploy", "label": "Deploy"},
        {"id": "cancel", "label": "Cancel"},
    ],
    "timeout": 45,
}

with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
    client.settimeout(55)
    client.connect("/tmp/decky-dialogs.sock")
    client.sendall(json.dumps(request).encode("utf-8") + b"\n")

    response = b""
    while b"\n" not in response:
        chunk = client.recv(4096)
        if not chunk:
            break
        response += chunk

result = json.loads(response.decode("utf-8"))

if result["status"] == "selected" and result.get("button") == "deploy":
    print("Deployment approved")
else:
    print(f"Deployment not approved: {result['status']}")
```

## Build on the Steam Deck and deploy

From the development machine, synchronize the source tree to the Deck. Run this from the project root:

```bash
rsync -av --delete \
  --exclude='node_modules/' \
  --exclude='.git/' \
  --exclude='dist/' \
  ./ \
  deck@192.168.1.135:/home/deck/Games/Switch_Emu/steam_helper/plugins/decky-dialogs/
```

Build and install on the Deck:

```bash
ssh deck@192.168.1.135 '
  set -e

  SRC=/home/deck/Games/Switch_Emu/steam_helper/plugins/decky-dialogs
  DEST=/home/deck/homebrew/plugins/DeckyDialogs

  cd "$SRC"
  pnpm install --frozen-lockfile
  rm -rf dist
  pnpm build
  test -f dist/index.js

  sudo mkdir -p "$DEST"
  sudo rsync -a --delete \
    --exclude="node_modules/" \
    --exclude=".git/" \
    "$SRC/" "$DEST/"

  sudo chmod +x "$DEST/decky-dialogs"
  mkdir -p /home/deck/.local/bin
  ln -sfn "$DEST/decky-dialogs" /home/deck/.local/bin/decky-dialogs

  sudo systemctl restart plugin_loader.service
'
```

Wait several seconds for Decky and the Steam frontend to reconnect. Enable **Decky Dialogs** in Decky if it is disabled.

Verify the socket and helper:

```bash
ssh deck@192.168.1.135 '
  test -S /tmp/decky-dialogs.sock && echo "Socket ready"
  /home/deck/.local/bin/decky-dialogs "Installation complete" "Decky Dialogs is ready."
'
```

## Migrating from SSH Notify

After installing Decky Dialogs, remove the old plugin, helper, and stale socket:

```bash
ssh deck@192.168.1.135 '
  sudo rm -rf /home/deck/homebrew/plugins/SSHNotify
  rm -f /home/deck/.local/bin/deck-notify
  sudo systemctl restart plugin_loader.service
  sudo rm -f /tmp/decky-ssh-notify.sock
'
```

If your old source checkout is no longer needed, it can also be removed:

```bash
ssh deck@192.168.1.135 \
  'rm -rf /home/deck/Games/Switch_Emu/steam_helper/plugins/ssh-notify'
```

## Troubleshooting

### `No such file or directory`

If the helper reports error 2, the socket usually does not exist. Confirm the plugin is installed and enabled:

```bash
ssh deck@192.168.1.135 '
  ls -l /home/deck/.local/bin/decky-dialogs
  ls -la /home/deck/homebrew/plugins/DeckyDialogs
  test -S /tmp/decky-dialogs.sock && echo ready || echo missing
'
```

Inspect Decky logs:

```bash
ssh deck@192.168.1.135 \
  'sudo journalctl -u plugin_loader.service --since "10 minutes ago" --no-pager | tail -n 200'
```

### Architecture notes

- Toasts and dialogs share one asynchronous Unix socket.
- Dialog connections remain open while waiting for a result.
- The frontend queues simultaneous dialogs instead of stacking them.
- The backend enforces timeouts and asks the frontend to close stale dialogs.
- Socket clients and pending requests are closed during plugin unload.
